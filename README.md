# AI News Aggregator

Daily AI brief from **free, verified sources** — official blogs (RSS), arXiv papers, and
Hacker News — synthesized by Google Gemini (free tier) and exported as PDF + Markdown.

X/Twitter signals are included as a **supplementary, clearly-labeled unverified section**
(via Gemini Google-Search grounding), because X no longer offers free read access and
scrapers (snscrape etc.) are dead.

## How it works

```
RSS blogs ─┐
arXiv ─────┼─► dedupe/sort ─► Gemini synthesis ─► PDF + MD report
HN ────────┘                       ▲
X signals (unverified, optional) ──┘
```

## Setup

Requires Python 3.10+ and a free [Google AI Studio](https://aistudio.google.com/) API key.

```powershell
cd c:\Users\ylnha\Projects\ai-news-aggregator
..\.venv\ai_news\Scripts\Activate.ps1
poetry install            # or: pip install google-genai python-dotenv reportlab requests pyyaml
cp .env.example .env      # then put your GEMINI_API_KEY in .env
```

## Run

```powershell
poetry run python src/main.py
# or
.\run.ps1
```

Outputs land in `reports/` (`.pdf` + `.md`); raw fetched items are cached in `data/`.

## Configuration (`config/settings.yaml`)

- `sources.rss.feeds` — add/remove any RSS or Atom feed
- `sources.arxiv.categories` — arXiv categories (default cs.AI, cs.CL, cs.LG)
- `sources.hackernews` — search queries and minimum points
- `twitter.enabled` — set `false` to skip the unverified social section (saves quota)
- `gemini.model_candidates` — real model IDs tried in order (fallback on quota/404)

`config/twitter_accounts.json` — the accounts used for the social-signals search.

## Free-tier quota notes

A full run makes ~3-4 Gemini calls (2 social-signal batches + 1 synthesis +
1 model listing, which is free). Well within free-tier daily limits; the 15s
`batch_delay_seconds` keeps you under per-minute limits.

## Schedule daily (Windows Task Scheduler)

```powershell
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-File C:\Users\ylnha\Projects\ai-news-aggregator\run.ps1" -WorkingDirectory "C:\Users\ylnha\Projects\ai-news-aggregator"
$trigger = New-ScheduledTaskTrigger -Daily -At 7:30AM
Register-ScheduledTask -TaskName "AI News Daily Brief" -Action $action -Trigger $trigger
```

## Project structure

```
src/
  config.py             # YAML + .env configuration
  content_fetcher.py    # RSS/Atom, arXiv, Hacker News (stdlib XML, no scraper deps)
  twitter_fetcher.py    # Unverified social signals via Gemini search grounding
  gemini_synthesizer.py # Daily-brief synthesis (google-genai SDK)
  pdf_exporter.py       # Markdown -> PDF (escaped, links/bold/bullets rendered)
  main.py               # Orchestration
config/
  settings.yaml         # All knobs
  twitter_accounts.json # 50 accounts for social signals
```

## Troubleshooting

- **429 / quota errors** — synthesis automatically falls through `model_candidates`;
  if all fail, lower `sources.arxiv.max_results`, set `twitter.enabled: false`, or wait
  for the daily quota reset.
- **Empty social section** — normal; grounding only returns what it can find, and the
  model is instructed not to fabricate.
- **A feed stopped working** — feeds occasionally move; remove or replace the entry in
  `settings.yaml` (the run continues past failed feeds).
