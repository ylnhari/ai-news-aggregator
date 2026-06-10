# AI News Aggregator

Daily AI brief from **free, verified sources** — official blogs (RSS), arXiv papers, and
Hacker News — synthesized by Google Gemini (free tier) and exported as PDF + Markdown.

Covers all major AI players: Anthropic, OpenAI, Google DeepMind, Meta AI, Microsoft AI,
NVIDIA, Intel, Qualcomm, Hugging Face, AWS, and more.

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

Gemini model selection is automatic: the pipeline lists all models available to your API
key and tries them in preference order (newest/most capable first), falling through to the
next on quota or availability errors — so the run succeeds even when individual model
quotas are exhausted.

## Setup

Requires Python 3.10+ and a free [Google AI Studio](https://aistudio.google.com/) API key.

```bash
# Clone and enter the repo
git clone <repo-url>
cd ai-news-aggregator

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\Activate.ps1    # Windows PowerShell

# Install dependencies
pip install -e .
# or with Poetry: poetry install

# Configure your API key
cp .env.example .env
# Edit .env and set GEMINI_API_KEY=<your key>
```

## Run

```bash
python src/main.py
```

Outputs land in `reports/` (`.pdf` + `.md`); raw fetched items are cached in `data/`.

## Configuration (`config/settings.yaml`)

- `sources.rss.feeds` — add/remove any RSS or Atom feed (name + url)
- `sources.arxiv.categories` — arXiv categories (default: cs.AI, cs.CL, cs.LG, cs.CV, cs.NE)
- `sources.arxiv.max_results` — cap on papers per run (default 50)
- `sources.hackernews` — search queries and minimum points threshold
- `twitter.enabled` — set `false` to skip the unverified social section (saves quota)
- `twitter.search_grounding_models` — ordered list of models to try for social-signals search
- `gemini.model_candidates` — ordered list of models to try for synthesis
- `gemini.max_tokens` — synthesis output length (default 8000)

`config/twitter_accounts.json` — the accounts used for the social-signals search.

## Model selection & quota

A full run makes ~3 Gemini calls (social-signal batches + synthesis + one `models.list()`).
Both synthesis and social-signals support **automatic model fallback**: if a model returns
`429 / 404 / 503` the next candidate in the list is tried immediately.

The default order (configurable in `settings.yaml`) is:

| Purpose | Model order |
|---|---|
| Synthesis | gemini-3-flash-preview → gemini-2.5-flash → gemini-flash-latest → gemini-3.1-flash-lite → … |
| Social signals | gemini-flash-latest → gemini-3.1-flash-lite → gemini-3-flash-preview → gemini-2.5-flash → … |

Models with `Supports Search Grounding: Yes` are required for social signals; synthesis
has no such constraint. Run `python scripts/list_models.py` (if present) to refresh your
local model status.

## Scheduling (optional)

### Linux / macOS — cron

```bash
# Run at 07:30 every day
30 7 * * * cd /path/to/ai-news-aggregator && .venv/bin/python src/main.py >> logs/cron.log 2>&1
```

### Windows — Task Scheduler

```powershell
$action  = New-ScheduledTaskAction -Execute "python" `
             -Argument "src\main.py" `
             -WorkingDirectory "C:\path\to\ai-news-aggregator"
$trigger = New-ScheduledTaskTrigger -Daily -At 7:30AM
Register-ScheduledTask -TaskName "AI News Daily Brief" -Action $action -Trigger $trigger
```

## Project structure

```
src/
  config.py             # YAML + .env configuration
  content_fetcher.py    # RSS/Atom, arXiv, Hacker News (stdlib XML, no scraper deps)
  twitter_fetcher.py    # Unverified social signals via Gemini search grounding
  gemini_synthesizer.py # Daily-brief synthesis (google-genai SDK, multi-model fallback)
  pdf_exporter.py       # Markdown -> PDF (links, bold, bullets rendered)
  main.py               # Orchestration
config/
  settings.yaml         # All knobs
  twitter_accounts.json # Accounts for social signals
```

## Troubleshooting

- **429 / quota errors** — both synthesis and social signals automatically try the next
  model in the configured list; if all fail, lower `sources.arxiv.max_results`, set
  `twitter.enabled: false`, or wait for the daily quota reset.
- **Empty social section** — normal; grounding only returns what it actually finds, and
  the model is instructed not to fabricate.
- **A feed stopped working** — feeds occasionally move; remove or update the entry in
  `settings.yaml`. Failing feeds are logged as warnings and skipped; the run continues.
- **Model not found (404)** — the pipeline falls through to the next candidate
  automatically. To add new models, append them to `gemini.model_candidates` in
  `settings.yaml`.
