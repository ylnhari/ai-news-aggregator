# AI News Aggregator

> Your free, personal AI-industry analyst. Every day: one PDF brief covering everything
> that matters in AI — official announcements, fresh research, and community buzz.

[![CI](https://img.shields.io/badge/CI-GitHub_Actions-blue)](.github/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)

**100% free to operate.** Every data source is free (RSS, arXiv API, Hacker News API),
and synthesis runs on the free Gemini tier with automatic multi-model fallback so quota
exhaustion never kills a run.

## What you get

A daily **PDF + Markdown brief** with:
- **Executive Summary** — the 2-3 things a practitioner must know today
- **Top Stories** — with context, why-it-matters, and source links
- **Research Radar** — notable new arXiv papers, incremental ones filtered out
- **Community Pulse** — what Hacker News is discussing
- **Social Signals** — what AI leaders are posting (clearly labeled unverified)
- **Action Items** — concrete things to read, try, or evaluate

## Sources (all free)

| Type | Sources |
|---|---|
| Official blogs (RSS) | OpenAI, Google DeepMind, Google AI, Meta Engineering, Meta Newsroom, Microsoft Research, NVIDIA, Intel (×2), AWS ML, Hugging Face, Simon Willison |
| Research | arXiv: cs.AI, cs.CL, cs.LG, cs.CV, cs.NE (50 newest papers) |
| Community | Hacker News stories ≥30 points matching 11 AI queries |
| Social (unverified) | 52 accounts — researchers, CEOs, labs — via Gemini web search |

Anthropic and Qualcomm have no public RSS; they're covered via Hacker News queries and
the social-signals accounts.

## Quick start

Requires Python 3.10+ and a free [Google AI Studio](https://aistudio.google.com/) API key.

```bash
git clone <repo-url>
cd ai-news-aggregator

python -m venv .venv
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\Activate.ps1    # Windows PowerShell

pip install -r requirements.txt

cp .env.example .env             # then put your GEMINI_API_KEY in .env

python src/main.py
```

Reports land in `reports/` (`.pdf` + `.md`); raw fetched items are cached in `data/`.

### CLI options

```
python src/main.py [--days-back N] [--no-social] [--output-dir DIR]
                   [--keep-cache-days N] [--quiet]
```

- `--days-back N` — widen the lookback window (e.g. `7` for a weekly digest)
- `--no-social` — skip the unverified social section (faster, saves quota)
- `--keep-cache-days N` — auto-delete raw cache older than N days (default 14)

## Run it free in the cloud (no laptop needed)

Fork this repo, then:

1. **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `GEMINI_API_KEY`, value: your free key
2. Enable workflows in the **Actions** tab.

[`daily-report.yml`](.github/workflows/daily-report.yml) runs every day at 06:30 UTC
(or on demand via *Run workflow*) and uploads the PDF + Markdown as an artifact you can
download from the run page. GitHub Actions is free for public repos.

## Configuration (`config/settings.yaml`)

| Setting | What it does |
|---|---|
| `sources.rss.feeds` | Add/remove any RSS or Atom feed (name + url) |
| `sources.arxiv.categories` | arXiv categories to poll |
| `sources.arxiv.max_results` | Papers per run (default 50) |
| `sources.hackernews.queries` | HN search terms |
| `sources.hackernews.min_points` | Minimum story score (default 30) |
| `twitter.enabled` | `false` to disable social signals entirely |
| `twitter.search_grounding_models` | Ordered model list for social search |
| `gemini.model_candidates` | Ordered model list for synthesis |
| `gemini.max_tokens` | Synthesis output length (default 8000) |
| `report.title` | Your brief's title |

`config/twitter_accounts.json` holds the social-signals account list — edit freely.

## How model fallback works

The pipeline never depends on a single model:

1. One `models.list()` call discovers what your key can access (free, no quota cost).
2. The configured preference list is intersected with what's actually available.
3. On any `429` (quota), `404` (removed model), or `503` (overloaded), the next
   candidate is tried immediately. The run only fails if *every* model fails.

Refresh your view of available models any time:

```bash
python scripts/list_models.py
```

## Architecture

```
RSS blogs ─┐
arXiv ─────┼─► dedupe/sort ─► Gemini synthesis ─► PDF + MD report
HN ────────┘                       ▲    (model fallback chain)
X signals (unverified, optional) ──┘
```

```
src/
  config.py             # YAML + .env configuration, validation
  content_fetcher.py    # RSS/Atom, arXiv, Hacker News (stdlib XML, retrying HTTP session)
  twitter_fetcher.py    # Unverified social signals via Gemini search grounding
  gemini_synthesizer.py # Daily-brief synthesis with multi-model fallback
  pdf_exporter.py       # Markdown -> PDF (escaped; links/bold/bullets rendered)
  main.py               # CLI + orchestration
config/
  settings.yaml         # All knobs
  twitter_accounts.json # Accounts for social signals
scripts/
  list_models.py        # Show Gemini models available to your key
tests/                  # pytest suite (parsing, escaping, config)
```

## Engine (`engine/`) — the deterministic collection core

`engine/` is a newer, self-contained pipeline that runs alongside the legacy
`src/` brief. It is **stdlib-only** (`sqlite3`, `urllib`, `xml.etree`, `json`,
`html.parser`) — **no third-party dependencies and no LLM calls anywhere**. It
collects signal, stores it with cross-run watermarks, tags beats, heuristically
pre-ranks, and writes a Markdown digest. All editorial judgment (clustering into
events, significance scoring, pitching) is deliberately left to a *later* LLM
pass that lives outside this engine — the pre-rank is honest about being a first
pass.

### Open-source posture

The engine ships **generic machinery only**. The source list, beat weights, and
output location are read from config that points at **a private config repo** you
supply — none of that lives here. This repo ships `engine.config.example.json`
and `registry/` documentation only; your real registry and digests live in your
own private repo.

### Design

```
sources.json (private) ─► transports ─► SQLite store ─► beats ─► rank ─► digest.md
     registry              rss/hn/hf/       items +       keyword   heuristic   + INDEX.md
                           arxiv/greenhouse/ watermarks     tags     pre-rank
                           html-diff
```

- **Watermarks, not "last 24h".** Each source is fetched *since its last
  successful run* (first run falls back to a configurable window). A missed day
  self-heals — the next run covers the whole gap. Failures never advance a
  source's watermark and never crash a run; they're recorded per-source and
  surfaced in the digest's "Mesh health" footer.
- **Transports** (`engine/transports/`), one per kind, all returning normalized
  item dicts: `rss` (RSS2 + Atom, incl. GitHub `releases.atom` with per-repo
  expansion), `hn` (HN Algolia search-by-date, points threshold), `hf` (HF new
  models, keyword-collapsed firehose), `arxiv` (Atom query in window),
  `greenhouse` (jobs JSON → item per *new* posting vs snapshot), `htmldiff`
  (strip tags, extract link/title pairs, diff vs last snapshot → new links).
  All requests use a real browser User-Agent and a 30s timeout. Transports that
  need a real browser (Cloudflare/WAF-gated, JS-only) are marked unsupported in
  the registry and handled elsewhere.

```
engine/
  __main__.py     # CLI: collect | digest | run
  config.py       # engine.config.json loader (paths, weights, keyword maps)
  registry.py     # reads the private registry's machine-JSON (never the YAML)
  store.py        # SQLite: items, source_runs (watermarks), snapshots; 90-day prune
  collect.py      # orchestrator: run each transport, tag, store, record outcomes
  beats.py        # keyword→beat tagger (additive to source-default beats)
  rank.py         # heuristic pre-rank + naive near-duplicate grouping
  digest.py       # Markdown digest (top stories + by-beat + mesh health) + INDEX
  transports/     # rss, hn, hf, arxiv, greenhouse, htmldiff, http (shared fetch)
engine.config.example.json   # copy to engine.config.json (gitignored) and edit
scripts/register_task.ps1    # register the daily Windows Scheduled Task
```

### Config & running

```bash
cp engine.config.example.json engine.config.json   # then edit paths + weights
python -m engine collect     # fetch every enabled source since its watermark
python -m engine digest      # build a digest from recently-fetched items (--hours N)
python -m engine run         # collect + digest (the scheduled daily job)
```

`engine.config.json` keys: `signaldesk_dir` (your private config repo),
`db_path`, `window_hours_first_run`, `beat_weights`, `hf_keywords`,
`beat_keywords`. The registry the engine reads is `<config repo>/registry/
sources.json` — a strict-JSON twin of a human-authored policy doc. Schedule the
daily run with `scripts/register_task.ps1` (writes a "signaldesk-daily-collect"
task at 08:30, catch-up on miss).

## Security notes

- Scraped content is treated as untrusted: it's XML-escaped before PDF rendering, and
  the synthesis prompt instructs the model to treat item text strictly as data
  (prompt-injection hardening).
- URLs returned by the LLM for social signals are validated to `http(s)` only before
  they become clickable PDF links.
- Your API key lives only in `.env` (gitignored) or a GitHub Actions secret.

## Scheduling locally (optional)

**Linux/macOS cron:**
```bash
30 7 * * * cd /path/to/ai-news-aggregator && .venv/bin/python src/main.py --quiet
```

**Windows Task Scheduler:**
```powershell
$action  = New-ScheduledTaskAction -Execute "python" -Argument "src\main.py" `
             -WorkingDirectory "C:\path\to\ai-news-aggregator"
$trigger = New-ScheduledTaskTrigger -Daily -At 7:30AM
Register-ScheduledTask -TaskName "AI News Daily Brief" -Action $action -Trigger $trigger
```

## Troubleshooting

- **`Configuration problem: GEMINI_API_KEY ...`** — copy `.env.example` to `.env` and
  add your key from [aistudio.google.com](https://aistudio.google.com/).
- **429 / quota errors** — fallback handles these automatically; if every model is
  exhausted, wait for the daily reset or set `twitter.enabled: false`.
- **Empty social section** — normal; web-search grounding only returns what it finds,
  and the model is instructed never to fabricate.
- **A feed stopped working** — feeds move; failing feeds are logged and skipped, the
  run continues. Update the URL in `settings.yaml` when convenient.

## Contributing

PRs welcome — especially new high-quality feed URLs, better prompts, and additional
output formats (HTML email, Slack webhook, …). Run `pytest` before submitting.

## License

[MIT](LICENSE)
