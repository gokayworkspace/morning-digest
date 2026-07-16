"""
fetchers.py — everything that pulls data from the outside world.

Design rule for the whole project: a fetcher NEVER crashes the digest.
Any network/parse error is caught, logged to stderr, and returns [].
A morning digest with one missing section beats no digest at all.

Every fetcher returns a list of Item dicts:
    {
        "title":     str,
        "url":       str,
        "source":    str,            # display name from sources.yaml
        "published": datetime | None (timezone-aware UTC when known),
        "extra":     str | None,     # e.g. "312 points" for HN
    }
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

import feedparser
import requests


def _log(msg: str) -> None:
    print(f"[fetch] {msg}", file=sys.stderr)


def _to_datetime(struct) -> datetime | None:
    """feedparser gives time.struct_time (UTC); convert to aware datetime."""
    if struct is None:
        return None
    try:
        return datetime.fromtimestamp(time.mktime(struct), tz=timezone.utc)
    except (OverflowError, ValueError):
        return None


# ── RSS / Atom (also used for Reddit, which is just Atom) ────────────

def fetch_rss(feed_cfg: dict, settings: dict) -> list[dict]:
    name = feed_cfg.get("name", feed_cfg["url"])
    try:
        # Fetch with requests ourselves (feedparser's own fetching has no
        # timeout control), then hand the bytes to feedparser.
        resp = requests.get(
            feed_cfg["url"],
            timeout=settings.get("request_timeout", 15),
            headers={"User-Agent": settings.get("user_agent", "morning-digest")},
        )
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
    except Exception as e:  # noqa: BLE001 — deliberate catch-all, see module docstring
        _log(f"{name}: FAILED ({e})")
        return []

    items = []
    for entry in parsed.entries:
        title = (entry.get("title") or "").strip()
        url = entry.get("link") or ""
        if not title or not url:
            continue
        items.append({
            "title": title,
            "url": url,
            "source": name,
            "published": _to_datetime(
                entry.get("published_parsed") or entry.get("updated_parsed")
            ),
            "extra": None,
        })
    _log(f"{name}: {len(items)} items")
    return items


# Reddit's .rss endpoints are plain Atom — same code path, kept separate
# in the config so we can special-case it later (e.g. score filtering
# via the JSON API in Phase 3).
fetch_reddit = fetch_rss


# ── Reddit via the JSON API ──────────────────────────────────────────
# The anonymous .rss endpoint is aggressively throttled (429s), and
# some networks are blocked outright (403). The JSON endpoint with a
# descriptive User-Agent is more tolerant — and with free OAuth
# credentials it becomes fully reliable.
#
# OAuth setup (once, ~5 min):
#   1. https://www.reddit.com/prefs/apps → "create another app"
#   2. type: "script", name/redirect can be anything (http://localhost)
#   3. export REDDIT_CLIENT_ID=<the id under the app name>
#      export REDDIT_CLIENT_SECRET=<the secret>
# If the env vars are absent, we fall back to anonymous access.

_reddit_token: str | None = None   # cached for the run: 1 token, N subreddits


def _get_reddit_token(settings: dict) -> str | None:
    global _reddit_token
    if _reddit_token:
        return _reddit_token
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    try:
        resp = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            data={"grant_type": "client_credentials"},   # app-only, read-only
            auth=(client_id, client_secret),
            timeout=settings.get("request_timeout", 15),
            headers={"User-Agent": settings.get(
                "user_agent", "morning-digest/0.1 (personal news script)")},
        )
        resp.raise_for_status()
        _reddit_token = resp.json()["access_token"]
        _log("reddit: OAuth token acquired")
        return _reddit_token
    except Exception as e:  # noqa: BLE001
        _log(f"reddit: OAuth FAILED ({e}), falling back to anonymous")
        return None


def fetch_reddit_json(feed_cfg: dict, settings: dict) -> list[dict]:
    name = feed_cfg.get("name", feed_cfg.get("subreddit", "reddit"))
    sub = feed_cfg["subreddit"]
    min_score = feed_cfg.get("min_score", 20)

    headers = {"User-Agent": settings.get(
        "user_agent", "morning-digest/0.1 (personal news script)")}
    token = _get_reddit_token(settings)
    if token:
        base = "https://oauth.reddit.com"          # authenticated endpoint
        headers["Authorization"] = f"Bearer {token}"
    else:
        base = "https://www.reddit.com"            # anonymous fallback
    url = f"{base}/r/{sub}/top.json"

    data = None
    for attempt in (1, 2):                         # one retry on rate-limit
        try:
            resp = requests.get(
                url,
                params={"t": "day", "limit": 25, "raw_json": 1},
                timeout=settings.get("request_timeout", 15),
                headers=headers,
            )
            if resp.status_code == 429 and attempt == 1:
                _log(f"{name}: rate-limited, retrying in 5s…")
                time.sleep(5)
                continue
            resp.raise_for_status()
            data = resp.json()
            break
        except Exception as e:  # noqa: BLE001
            _log(f"{name}: FAILED ({e})")
            return []
    if data is None:
        _log(f"{name}: FAILED (rate-limited twice)")
        return []

    candidates = []
    for child in data.get("data", {}).get("children", []):
        post = child.get("data", {})
        if post.get("stickied"):
            continue
        candidates.append({
            "title": (post.get("title") or "").strip(),
            "url": "https://www.reddit.com" + post.get("permalink", ""),
            "source": name,
            "published": datetime.fromtimestamp(
                post.get("created_utc", 0), tz=timezone.utc),
            "extra": f"▲{post.get('score', 0)}",
            "_score": post.get("score", 0),
        })

    items = [c for c in candidates if c["_score"] >= min_score]
    if items:
        _log(f"{name}: {len(items)} items (≥{min_score} upvotes)")
    elif candidates:
        # Quiet day on the subreddit: min_score is a PREFERENCE, not a
        # cliff. Better three modest posts than a vanished section.
        fallback = feed_cfg.get("fallback_count", 3)
        items = sorted(candidates, key=lambda c: c["_score"], reverse=True)[:fallback]
        _log(f"{name}: nothing ≥{min_score} upvotes, "
             f"falling back to top {len(items)}")
    else:
        _log(f"{name}: 0 posts returned")

    for item in items:
        item.pop("_score", None)   # internal field, don't leak downstream
    return items


# ── Hacker News via the Algolia API (no key needed) ─────────────────

def fetch_hackernews(feed_cfg: dict, settings: dict) -> list[dict]:
    """
    Two modes, chosen by config:
      - front page (default):        {type: hackernews, min_points: 100}
      - keyword search:              {type: hackernews, query: "3D printing",
                                      min_points: 10, since_hours: 48}
    Search mode finds community-upvoted stories on a topic — a decent
    stand-in for a subreddit, powered by an API that wants to be used.
    """
    name = feed_cfg.get("name", "Hacker News")
    min_points = feed_cfg.get("min_points", 100)
    query = feed_cfg.get("query")

    params = {"hitsPerPage": 30}
    if query:
        since = int(time.time()) - feed_cfg.get("since_hours", 48) * 3600
        params.update({
            "query": query,
            "tags": "story",
            "numericFilters": f"points>={min_points},created_at_i>={since}",
        })
    else:
        params["tags"] = "front_page"

    try:
        resp = requests.get(
            "https://hn.algolia.com/api/v1/search",
            params=params,
            timeout=settings.get("request_timeout", 15),
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", [])
    except Exception as e:  # noqa: BLE001
        _log(f"{name}: FAILED ({e})")
        return []

    items = []
    for hit in hits:
        points = hit.get("points") or 0
        if points < min_points:
            continue
        title = (hit.get("title") or "").strip()
        # Ask HN / Show HN posts have no external URL — link to the thread.
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        published = None
        if hit.get("created_at_i"):
            published = datetime.fromtimestamp(hit["created_at_i"], tz=timezone.utc)
        items.append({
            "title": title,
            "url": url,
            "source": name,
            "published": published,
            "extra": f"{points} pts, {hit.get('num_comments', 0)} comments",
        })
    _log(f"{name}: {len(items)} items (≥{min_points} pts"
         + (f", query='{query}'" if query else "") + ")")
    return items


# ── FX rates via open.er-api.com (free, no key) ─────────────────────

def fetch_fx_rates(feed_cfg: dict, settings: dict) -> list[dict]:
    base = feed_cfg.get("base", "EUR")
    symbols = feed_cfg.get("symbols", ["TRY", "USD"])
    try:
        resp = requests.get(
            f"https://open.er-api.com/v6/latest/{base}",
            timeout=settings.get("request_timeout", 15),
        )
        resp.raise_for_status()
        rates = resp.json().get("rates", {})
    except Exception as e:  # noqa: BLE001
        _log(f"FX: FAILED ({e})")
        return []

    items = []
    for sym in symbols:
        if sym in rates:
            items.append({
                "title": f"1 {base} = {rates[sym]:.2f} {sym}",
                "url": f"https://www.xe.com/currencyconverter/convert/?From={base}&To={sym}",
                "source": "FX",
                "published": datetime.now(tz=timezone.utc),  # always "fresh"
                "extra": None,
            })
    _log(f"FX: {len(items)} rates")
    return items


# ── Dispatch table: config `type` → function ─────────────────────────
# Adding a new source type = write a function + one line here.

FETCHERS = {
    "rss": fetch_rss,
    "reddit": fetch_reddit,
    "reddit_json": fetch_reddit_json,
    "hackernews": fetch_hackernews,
    "fx_rates": fetch_fx_rates,
}