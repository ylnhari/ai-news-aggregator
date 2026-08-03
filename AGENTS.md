# Agent Instructions — ai-news-aggregator

> **Setup:** copy `AGENTIC.local.md.example` → `AGENTIC.local.md` (gitignored) for machine-local notes. It is read only when the active host has a verified adapter that explicitly loads it. Personal/global preferences live in your own agent's global instructions file.

## Role
Act as a Python backend engineer focused on cost-free, production-quality data pipelines.

## Context
Daily AI news aggregator. Free-tier only — no paid APIs, ever.

**TWO pipelines coexist — know which one you're editing:**
- `engine/` — the CURRENT collection core: stdlib-only, zero LLM calls, SQLite
  watermarks, cross-day story ledger (`stories` table + anchor matching), HTML
  site. Config: `engine.config.json` (copy the example; it points at
  `examples/workspace` out of the box). Tests:
  `python -m unittest tests.test_engine_core`. No uv needed (zero deps).
- `src/` — the LEGACY brief generator (below). Working and CI-tested; uses
  third-party deps → always `uv sync` / `uv run …` (uv.lock is authoritative;
  poetry is gone).

**Legacy pipeline:** RSS/Atom + arXiv API + Hacker News Algolia → Gemini synthesis (PDF + Markdown output)

**What's not here:** Twitter API (no free read API). Social signals come from Gemini search-grounding only, clearly labeled "UNVERIFIED" in the report.

## Architecture

```
src/
  config.py             # YAML + .env config + validate()
  content_fetcher.py    # RSS/Atom (stdlib XML), arXiv, HN — all return verified=True NewsItem
  twitter_fetcher.py    # SocialSignalsFetcher — Gemini search grounding, verified=False
  gemini_synthesizer.py # Synthesis with multi-model fallback
  pdf_exporter.py       # Markdown → PDF via ReportLab; XML-escaped
  main.py               # Orchestration + CLI (--days-back, --no-social, --output-dir, --quiet)
config/
  settings.yaml         # All tunable knobs
  twitter_accounts.json # 52 social-signal accounts
scripts/
  list_models.py        # Show Gemini models available to your key
tests/
```

## Hard Rules

1. **Never use `google-generativeai`** — it's EOL. Use `google-genai ^2.8.0` only.
2. **No feedparser** — use stdlib `xml.etree.ElementTree` for RSS/Atom parsing.
3. **Multi-model fallback is mandatory.** Model selection = one `models.list()` call intersected with `settings.yaml` preference list. Fall through on APIError codes 429/404/503.
4. **XML-escape all external content** before passing to ReportLab (`xml.sax.saxutils.escape`).
5. **No paid dependencies.** Check before adding anything to pyproject.toml.
6. **Read keys from the `Config` class.** (Global secret rules apply — see your own agent's global instructions file.)

## Key Patterns

- Use the `google-genai` SDK (`from google import genai`) — never `google-generativeai` (EOL).
- Model selection = one `models.list()` call intersected with the `settings.yaml` preference list; fall through to the next model on `APIError` codes 429/404/503.
- `settings.yaml` is the single source of truth for model lists and source configs.

## Credentials
- `GEMINI_API_KEY` in `.env` (gitignored). Source: https://aistudio.google.com/
- No other credentials required — arXiv and HN are auth-free.

## Running
```bash
uv sync                                  # install deps (creates .venv automatically)
cp .env.example .env                     # add GEMINI_API_KEY
uv run python src/main.py
uv run python src/main.py --no-social --days-back 3
```

## When editing
- Test XML escaping changes: `pytest tests/`
- Validate model fallback logic before touching `gemini_synthesizer.py` or `twitter_fetcher.py`
- `settings.yaml` is the single source of truth for model lists and source configs
