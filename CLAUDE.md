# Claude Instructions — ai-news-aggregator

> **Setup:** copy `CLAUDE.local.md.example` → `CLAUDE.local.md` (gitignored, auto-loaded) and fill in your machine-local values. Personal/global preferences live in your user-level `~/.claude/CLAUDE.md`.

## Role
Act as a Python backend engineer focused on cost-free, production-quality data pipelines.

## Context
Daily AI news aggregator. Free-tier only — no paid APIs, ever.

**Pipeline:** RSS/Atom + arXiv API + Hacker News Algolia → Gemini synthesis (PDF + Markdown output)

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
6. **Read keys from the `Config` class.** (Global secret rules apply: see ~/.claude/CLAUDE.md.)

## Key Patterns

- Use the `google-genai` SDK (`from google import genai`) — never `google-generativeai` (EOL).
- Model selection = one `models.list()` call intersected with the `settings.yaml` preference list; fall through to the next model on `APIError` codes 429/404/503.
- `settings.yaml` is the single source of truth for model lists and source configs.

## Credentials
- `GEMINI_API_KEY` in `.env` (gitignored). Source: https://aistudio.google.com/
- No other credentials required — arXiv and HN are auth-free.

## Running
```bash
python -m venv .venv && .venv\Scripts\Activate.ps1  # or activate.ps1
pip install -r requirements.txt
cp .env.example .env  # add GEMINI_API_KEY
python src/main.py
python src/main.py --no-social --days-back 3
```

## When editing
- Test XML escaping changes: `pytest tests/`
- Validate model fallback logic before touching `gemini_synthesizer.py` or `twitter_fetcher.py`
- `settings.yaml` is the single source of truth for model lists and source configs
