# ☕ morning-digest

A personal morning briefing that emails itself to me every day at 08:12
— world news, Hacker News, robotics/embedded, AI, Netherlands, markets +
EUR/TRY, 3D printing, and startups — fetched from feeds I pick, then
**curated by an LLM editor** that knows my interests: it selects
highlights with a one-line "why this matters to you", trims every
section to what's worth reading, and picks a daily long-read rabbit
hole. Runs entirely on GitHub Actions; my laptop can stay closed.

## How it works

```
sources.yaml ─► fetchers.py ─► digest.py (filter/dedupe) ─► rank.py (LLM) ─► render.py ─► email + out/
                                                              ▲
                                                         profile.md
```

- **`sources.yaml`** — every feed, cap, and freshness window. Adding a
  source is three lines of YAML, never code.
- **`fetchers.py`** — RSS/Atom, the Hacker News Algolia API (front page
  *and* keyword search), and FX rates, behind a dispatch table. A dead
  feed logs a warning and the digest carries on; failures are listed in
  the email footer.
- **`digest.py`** — freshness window, cross-section dedupe, per-source
  caps (no outlet monopolizes a section), sponsored-content blocklist.
- **`rank.py`** — one Groq API call ranks ~90 items against
  **`profile.md`** (the editor's brief). The model only ever returns
  item IDs from a numbered catalog — titles and URLs always come from
  fetched data, so hallucinated links are structurally impossible. Any
  failure falls back to the unranked digest; a sanity floor rejects
  over-aggressive cuts.
- **`render.py` / `emailer.py`** — markdown → HTML email via SMTP.
- **📡 Radar** — a special section (ASML/semiconductors, Home Assistant,
  Apple, gadgets) that stays *empty by design* most days: the editor
  only surfaces items that genuinely strike.

## Automation

`.github/workflows/digest.yml` runs daily on a cron (05:12 UTC), builds
and emails the digest, and commits it to `out/` — making the repo a
browsable archive of every issue. The bot rebases before pushing so it
never races my own commits. Credentials (Gmail app password, Groq key)
live in repo secrets; nothing sensitive is in the code.

## Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export GROQ_API_KEY=...        # optional: skip for an unranked digest
python3 digest.py              # prints + writes out/digest-YYYY-MM-DD.md
```

## Design rules the code lives by

- A failing feed (or a failing LLM) never kills the digest — degrade,
  log, deliver.
- Fetch, filter, rank, render, deliver: separate modules, one job each.
- Behavior lives in config (`sources.yaml`) and prompts (`profile.md`),
  not code. Retuning the entire product to a new description of me took
  zero logic changes.
- Ask the LLM strictly, parse its reply defensively, validate against
  our own data.

## Status

- [x] **Phase 1** — local script: RSS/HN/FX sources, filtering, markdown
- [x] **Phase 2** — GitHub Actions cron + HTML email + committed archive
- [x] **Phase 3** — LLM editor: profile-based ranking, highlights,
      radar tier, rabbit hole of the day
- Ideas: 👍/👎 feedback loop folded into the ranking prompt; weekly
  "best of the week" edition