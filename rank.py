"""
rank.py — the LLM editor (Phase 3).

Takes the fetched-and-filtered sections and asks a Groq-hosted model to
act as a personal news editor: pick highlights, trim and reorder each
section by relevance to the reader profile, drop same-story duplicates,
and choose one "rabbit hole" long read.

DESIGN RULE — the model never generates content we display as fact:
it only returns item IDs (from a numbered list we send) plus short
"why" strings. Titles, URLs, and sources always come from our own
fetched data, so a hallucinated link is structurally impossible.
Unknown IDs in the reply are ignored.

FAILURE RULE — any error (no key, network, bad JSON, empty reply)
returns None, and digest.py renders the plain unranked digest instead.
The morning email must never depend on an API being up.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Default model: openai/gpt-oss-120b. Groq deprecated the older
# llama-3.3-70b-versatile for free/dev tiers in June 2026. If this one
# is ever deprecated too, no code change needed — override with:
#   export GROQ_MODEL="whatever-is-current"
# (current list: https://console.groq.com/docs/models)
MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")


def _log(msg: str) -> None:
    print(f"[rank] {msg}", file=sys.stderr)


def _build_prompt(profile: str, catalog: list[dict]) -> list[dict]:
    system = f"""You are the editor of a one-person morning news digest.

READER PROFILE:
{profile}

You will receive a JSON list of news items, each with an integer "id",
a "section", a "title", a "source", and sometimes "extra" (points/score).

Your job:
1. "highlights": the 3 items MOST worth the reader's attention today,
   across all sections. Each gets a "why": one line, max 15 words,
   written for THIS reader specifically.
2. "sections": include EVERY input section title in your answer.
   Within each, choose the items worth keeping, best first. Drop items
   that are boring for this reader, near-duplicates of another item you
   already kept (same story from two outlets), ads, or pure press
   releases. Keep 2-5 items per section — this is a full morning
   briefing, not a shortlist; the reader still wants to know what
   happened in the world, the Netherlands, and the markets even when
   it isn't thrilling. Only leave a section empty if genuinely nothing
   qualifies. Aim for 15-25 kept items in total. Add a short "why"
   (max 12 words) ONLY when the relevance isn't obvious from the title.
   EXCEPTION — a section whose title contains "Radar" works the opposite
   way: keep items ONLY if genuinely striking for this reader per the
   profile's radar criteria (0-3 items); an empty Radar section is the
   normal, expected outcome most days. Radar items do not count toward
   the 15-25 target. A truly major radar story (e.g. big ASML news)
   should also be a highlight.
3. "rabbit_hole": ONE item that would reward 15+ minutes of reading —
   prefer deep technical write-ups. Include a one-line "why".

Highlights may also appear in their section. Never invent ids.

Respond with ONLY a JSON object, no markdown fences, no commentary:
{{"highlights": [{{"id": 1, "why": "..."}}],
  "sections": [{{"title": "...", "items": [{{"id": 2, "why": "..."}}]}}],
  "rabbit_hole": {{"id": 3, "why": "..."}}}}"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(catalog, ensure_ascii=False)},
    ]


def rank_digest(sections: list[dict], profile_path: Path,
                timeout: int = 60) -> dict | None:
    """
    sections: [{"title": str, "items": [Item, ...]}, ...] (post-filter)
    Returns {"sections": [...], "highlights": [...], "rabbit_hole": Item|None}
    or None on any failure (caller falls back to the unranked digest).
    """
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        _log("GROQ_API_KEY not set — skipping ranking")
        return None

    # Flatten to a catalog with stable ids; keep a lookup to map back.
    # Titles are truncated: the model only needs enough to judge
    # relevance, and Groq's free tier counts prompt + max_tokens
    # against a tokens-per-minute cap (exceeding it → HTTP 413).
    catalog, by_id = [], {}
    next_id = 1
    for section in sections:
        for item in section["items"]:
            entry = {"id": next_id, "section": section["title"],
                     "title": item["title"][:100], "source": item["source"]}
            if item.get("extra"):
                entry["extra"] = item["extra"]
            catalog.append(entry)
            by_id[next_id] = item
            next_id += 1
    if not catalog:
        return None

    profile = profile_path.read_text() if profile_path.exists() else ""

    try:
        body = {
            "model": MODEL,
            "temperature": 0.3,
            # Token budget balancing act: too small → the reasoning
            # model runs out mid-plan and returns a tiny JSON; too big →
            # Groq's free tier rejects the request with HTTP 413,
            # because prompt + max_tokens counts against a per-minute
            # token cap. ~3500 fits the cap and comfortably covers a
            # 25-item plan with reasoning_effort=low.
            "max_tokens": 3500,
            "response_format": {"type": "json_object"},
            "messages": _build_prompt(profile, catalog),
        }
        if MODEL.startswith("openai/gpt-oss"):
            body["reasoning_effort"] = "low"   # selection needs no deep thought
        resp = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json=body,
            timeout=timeout,
        )
        if resp.status_code >= 400:
            # Groq puts the real story (limits, token counts) in the
            # body — surface it instead of just the status code.
            _log(f"HTTP {resp.status_code}: {resp.text[:300]}")
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        plan = json.loads(content)
    except Exception as e:  # noqa: BLE001 — deliberate: fall back, never fail
        _log(f"FAILED ({e}) — falling back to unranked digest")
        return None

    def resolve(ref) -> dict | None:
        """
        Map an item reference back to a real item. The schema asks for
        {"id": 3, "why": "..."} but models sometimes return bare ids
        (3 or "3") — accept all dialects. Unknown/garbage refs → None.
        """
        why = ""
        if isinstance(ref, dict):
            rid = ref.get("id")
            why = (ref.get("why") or "").strip()
        else:
            rid = ref
        try:
            rid = int(rid)
        except (TypeError, ValueError):
            return None
        item = by_id.get(rid)
        if item is None:
            return None
        out = dict(item)
        if why:
            out["why"] = why
        return out

    highlights = [i for i in map(resolve, plan.get("highlights") or []) if i][:3]

    ranked_sections = []
    for sec in plan.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        items = [i for i in map(resolve, sec.get("items") or []) if i][:5]
        if items:
            ranked_sections.append({"title": sec.get("title", "?"),
                                    "items": items})
    rabbit = resolve(plan.get("rabbit_hole"))

    # Sanity: if the model returned nothing usable, don't pretend it worked.
    if not ranked_sections and not highlights:
        _log("model returned no usable ids — falling back")
        return None

    # Sanity floor: an "editor" that keeps almost nothing has failed at
    # the job (token starvation or over-aggression). Below the floor,
    # the full unranked digest is more useful than a gutted one.
    kept = sum(len(s["items"]) for s in ranked_sections)
    floor = min(10, max(4, len(catalog) // 6))
    if kept < floor:
        _log(f"only {kept}/{len(catalog)} items kept (floor {floor}) — "
             f"over-aggressive cut, falling back to unranked digest")
        return None

    _log(f"ranked: {kept}/{len(catalog)} items kept, "
         f"{len(highlights)} highlights"
         + (", rabbit hole set" if rabbit else ""))
    return {"sections": ranked_sections, "highlights": highlights,
            "rabbit_hole": rabbit}