# ☕ morning-digest

A personal morning briefing: world news, Hacker News, robotics/embedded,
AI, Netherlands, markets + EUR/TRY, useful 3D prints, and startup news —
one markdown digest, from feeds *you* pick.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python3 digest.py
```

Prints the digest and writes `out/digest-YYYY-MM-DD.md`.
Add or remove feeds by editing `sources.yaml` — no code changes needed.

## Architecture

```
sources.yaml → fetchers.py → digest.py (filter/dedupe) → render.py → out/
```

Rules the code lives by:

- A failing feed never kills the digest — it logs to stderr and moves on.
- Fetch, filter, and render are separate modules with one job each.
- All behavior (feeds, caps, freshness window) lives in config, not code.

## Roadmap

- [x] **Phase 1** — local script, RSS/HN/Reddit/FX sources, markdown output
- [ ] **Phase 2** — GitHub Actions cron at 08:00 TRT + email delivery,
      digests committed to the repo as a browsable archive
- [ ] **Phase 3** — LLM editor: rank items against a personal interest
      profile, cluster duplicate stories, add a "rabbit hole of the day"
