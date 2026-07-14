# Example workspace

A self-contained workspace so the engine runs on a fresh clone with zero
setup beyond copying the config:

```
cp engine.config.example.json engine.config.json   # already points here
uv run python -m engine run                        # or plain: python -m engine run
```

Layout (the engine reads/writes ONLY inside the workspace you point it at):

```
workspace/
  registry/sources.json   what to fetch (starter set: 7 plain-HTTP sources)
  digests/                generated daily digests (Markdown) + INDEX.md
  data/signal.db          SQLite: items, watermarks, stories (created on first run)
  pitches/                optional; *.md with `status: proposed` surface in digests
  site/                   generated HTML edition (index + one page per day)
```

For real use, copy this directory somewhere private (it will accumulate your
digests and story ledger), point `engine.config.json` at it, and grow
`registry/sources.json`. The engine repo itself never holds your data.
