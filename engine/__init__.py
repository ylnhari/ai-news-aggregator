"""signaldesk collection engine — deterministic, stdlib-only, no LLM.

The engine fetches sources listed in a private config repo's registry
(sources.json), normalizes them into items, tags beats, heuristically
pre-ranks, and writes a daily Markdown digest. All editorial judgment
(clustering into events, significance scoring, pitching) happens in a
later LLM pass that lives OUTSIDE this engine.

Public surface: `python -m engine {collect|digest|run}`.
"""

__all__ = ["config", "store", "registry", "beats", "rank", "digest", "collect"]
