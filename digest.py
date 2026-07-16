#!/usr/bin/env python3
"""
digest.py — entry point.  Run:  python3 digest.py

Pipeline (this shape is the whole architecture, memorize it):

    load config → fetch all sources → filter/dedupe → render → output

Phase 2 wraps this exact script in a GitHub Actions cron + email step.
Phase 3 inserts one `rank(items)` stage between filter and render.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from fetchers import FETCHERS
from render import render_markdown

HERE = Path(__file__).parent


# ── Filtering ────────────────────────────────────────────────────────

def _normalize_title(title: str) -> str:
    """Crude near-duplicate key: lowercase, alphanumerics only."""
    return "".join(ch for ch in title.lower() if ch.isalnum())


def filter_items(
    items: list[dict],
    max_items: int,
    max_age_hours: float,
    seen: set[str],
    max_per_source: int = 3,
    exclude_keywords: list[str] | None = None,
) -> list[dict]:
    """
    `seen` is shared across ALL sections (passed in by build_digest), so a
    story that appears in two feeds — e.g. TechCrunch publishing the same
    article to both its AI and Startups feeds — lands only once, in
    whichever section comes first in sources.yaml.

    `max_per_source` stops one chatty outlet from monopolizing a section
    (a Guardian feed that posts hourly would otherwise bury BBC entirely,
    because "newest first" rewards volume).

    `exclude_keywords` drops items whose title contains any of the given
    strings (case-insensitive) — mainly for feeds that mix in sponsored
    posts and ads.
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=max_age_hours)
    blocked = [k.lower() for k in (exclude_keywords or [])]

    fresh = []
    for item in items:
        pub = item.get("published")
        # Items with no date are kept: some feeds omit dates, and dropping
        # them silently loses good content. Phase 3 can get stricter.
        if pub is not None and pub < cutoff:
            continue
        title_lower = item["title"].lower()
        if any(k in title_lower for k in blocked):
            continue
        fresh.append(item)

    # Newest first; undated items sink to the bottom rather than vanish.
    # (Sort BEFORE capping so each source's cap keeps its newest items.)
    fresh.sort(
        key=lambda i: i["published"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    # Dedupe by normalized title + enforce the per-source cap in one pass.
    per_source: dict[str, int] = {}
    unique = []
    for item in fresh:
        key = _normalize_title(item["title"])
        if key in seen:
            continue
        if per_source.get(item["source"], 0) >= max_per_source:
            continue
        seen.add(key)
        per_source[item["source"]] = per_source.get(item["source"], 0) + 1
        unique.append(item)

    return unique[:max_items]


# ── Main ─────────────────────────────────────────────────────────────

def build_digest(config_path: Path) -> str:
    config = yaml.safe_load(config_path.read_text())
    settings = config.get("settings", {})
    default_max_age = settings.get("max_age_hours", 26)

    rendered_sections = []
    global_seen: set[str] = set()   # shared across sections → cross-section dedupe
    silent_feeds: list[str] = []    # feeds that produced nothing (failed OR empty)

    for section_cfg in config["sections"]:
        collected: list[dict] = []
        for feed_cfg in section_cfg.get("feeds", []):
            fetcher = FETCHERS.get(feed_cfg.get("type", "rss"))
            if fetcher is None:
                print(f"[warn] unknown feed type: {feed_cfg.get('type')}",
                      file=sys.stderr)
                continue
            items = fetcher(feed_cfg, settings)
            if not items:
                silent_feeds.append(feed_cfg.get("name", feed_cfg.get("url", "?")))
            collected.extend(items)

        rendered_sections.append({
            "title": section_cfg["title"],
            "items": filter_items(
                collected,
                max_items=section_cfg.get("max_items", 6),
                max_age_hours=section_cfg.get("max_age_hours", default_max_age),
                seen=global_seen,
                max_per_source=section_cfg.get(
                    "max_per_source", settings.get("max_per_source", 3)),
                exclude_keywords=(settings.get("exclude_keywords", [])
                                  + section_cfg.get("exclude_keywords", [])),
            ),
        })

    # Phase 3: LLM editor. Optional — if there's no GROQ_API_KEY or the
    # call fails, rank_digest returns None and we render the plain digest.
    from rank import rank_digest
    ranked = rank_digest(rendered_sections, HERE / "profile.md")
    if ranked:
        return render_markdown(ranked["sections"],
                               datetime.now(tz=timezone.utc),
                               silent_feeds=silent_feeds,
                               highlights=ranked["highlights"],
                               rabbit_hole=ranked["rabbit_hole"])

    return render_markdown(rendered_sections, datetime.now(tz=timezone.utc),
                           silent_feeds=silent_feeds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the morning digest.")
    parser.add_argument("--config", default=HERE / "sources.yaml", type=Path)
    parser.add_argument("--out-dir", default=HERE / "out", type=Path,
                        help="Directory for digest-YYYY-MM-DD.md")
    parser.add_argument("--no-file", action="store_true",
                        help="Print to stdout only, don't write a file")
    args = parser.parse_args()

    markdown = build_digest(args.config)
    print(markdown)

    if not args.no_file:
        args.out_dir.mkdir(exist_ok=True)
        out_path = args.out_dir / f"digest-{datetime.now():%Y-%m-%d}.md"
        out_path.write_text(markdown)
        print(f"[done] wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()