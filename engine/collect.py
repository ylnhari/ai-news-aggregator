"""Collection orchestrator: run every enabled source's transport, store new items.

Per source: compute the watermark, run its handler, tag beats, upsert items,
record the run outcome. A transport failure is caught and recorded in
source_runs (never crashes the run) and reported. Advancing the watermark only
on success means a failed source retries its whole gap next run.
"""

import time
from datetime import datetime, timezone

from . import beats
from .registry import load_registry, enabled_sources
from .store import Store
from .transports import HANDLERS


def collect(cfg, store: Store = None, verbose: bool = True):
    own_store = store is None
    store = store or Store(cfg.db_path)
    cfg._runtime_store = store  # transports that need snapshots (htmldiff/greenhouse)

    sources = load_registry(cfg.registry_path)
    active = enabled_sources(sources)
    trust_by_source = {s.id: s.trust for s in sources}

    run_started = datetime.now(timezone.utc)
    per_source = []  # (id, status, new_count, error)
    total_new = 0

    for src in active:
        handler = HANDLERS.get(src.handler)
        if handler is None:
            per_source.append((src.id, "skipped", 0, f"no handler '{src.handler}'"))
            continue
        since = store.watermark(src.id, cfg.window_hours_first_run)
        run_at = datetime.now(timezone.utc)
        try:
            raw_items = handler(src, since, cfg) or []
            new_count = 0
            for it in raw_items:
                if not it.get("url"):
                    continue
                it["source_id"] = src.id
                it["beats"] = beats.tag(it, cfg.beat_keywords)
                if store.upsert_item(it):
                    new_count += 1
            store.commit()
            store.record_run(src.id, "ok", "", success_time=run_at)
            per_source.append((src.id, "ok", new_count, ""))
            total_new += new_count
            if verbose:
                print(f"  [ok]   {src.id:22s} {new_count:3d} new "
                      f"({len(raw_items)} fetched, since {since:%Y-%m-%d %H:%M}Z)")
        except Exception as e:  # noqa: BLE001 — isolate per-source failures
            msg = f"{type(e).__name__}: {e}"[:300]
            store.record_run(src.id, "error", msg)
            per_source.append((src.id, "error", 0, msg))
            if verbose:
                print(f"  [ERR]  {src.id:22s} {msg}")
        time.sleep(0.3)  # be polite between sources

    # retention: drop full-detail items older than 90 days
    pruned = store.prune_items(90)

    report = {
        "run_started": run_started,
        "run_finished": datetime.now(timezone.utc),
        "per_source": per_source,
        "total_new": total_new,
        "pruned": pruned,
        "active_source_count": len(active),
    }
    if own_store:
        store.close()
    return report
