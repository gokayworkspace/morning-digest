"""
render.py — turn cleaned sections into markdown.

Kept dumb on purpose: no fetching, no filtering, no I/O. Give it data,
get a string. In Phase 2 the same output goes into an email body; in
Phase 3 an LLM-ranked structure will feed the exact same renderer.
"""

from __future__ import annotations

from datetime import datetime


def render_markdown(sections: list[dict], generated_at: datetime,
                    silent_feeds: list[str] | None = None,
                    highlights: list[dict] | None = None,
                    rabbit_hole: dict | None = None) -> str:
    """
    sections:     [{"title": str, "items": [Item, ...]}, ...]
    silent_feeds: names of feeds that produced zero items this run —
                  shown in the footer so a vanished section is
                  explainable from the email itself, without digging
                  through logs.
    highlights /  optional extras produced by the Phase 3 ranker; items
    rabbit_hole:  may carry a "why" field, rendered as an italic aside.
    """
    lines = [
        f"# ☕ Morning Digest — {generated_at.strftime('%A, %d %B %Y')}",
        "",
    ]

    def bullet(item: dict) -> str:
        suffix = f" _({item['extra']})_" if item.get("extra") else ""
        line = f"- [{item['title']}]({item['url']}) — {item['source']}{suffix}"
        if item.get("why"):
            line += f"\n  _{item['why']}_"
        return line

    if highlights:
        lines.append("## ☀️ Worth your attention")
        lines.append("")
        lines.extend(bullet(i) for i in highlights)
        lines.append("")

    for section in sections:
        items = section["items"]
        if not items:
            continue  # skip empty sections silently — no sad headers
        lines.append(f"## {section['title']}")
        lines.append("")
        lines.extend(bullet(i) for i in items)
        lines.append("")

    if rabbit_hole:
        lines.append("## 🕳️ Rabbit hole of the day")
        lines.append("")
        lines.append(bullet(rabbit_hole))
        lines.append("")

    lines.append("---")
    if silent_feeds:
        lines.append(f"_⚠️ No items from: {', '.join(silent_feeds)}_")
        lines.append("")
    lines.append(f"_Generated {generated_at.strftime('%H:%M UTC')} · "
                 f"morning-digest v0.1_")
    return "\n".join(lines) + "\n"