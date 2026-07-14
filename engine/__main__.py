"""CLI: python -m engine {collect|digest|run|prune|doctor|site}

  collect   fetch every enabled source since its watermark; store new items.
  digest    build a Markdown digest from recently-fetched items (--hours window).
  run       collect, then digest this run's new items (the scheduled daily job).
  prune     drop full-detail items older than --days (retention; OPERATIONS.md).
  doctor    self-check: config, registry, DB, connectivity, per-source table.
  site      render the HTML edition ("AI Signal") into signaldesk/site/.
"""

import argparse
import os
import sys
from datetime import timedelta

from .config import load_config, ConfigError, IST
from .store import Store, now_utc
from .collect import collect as run_collect
from .digest import build_digest, regenerate_index
from . import doctor as doctor_mod


def _summary(report):
    print()
    print(f"Collected: {report['total_new']} new item(s) across "
          f"{report['active_source_count']} active source(s); pruned {report['pruned']}.")
    skipped = [(sid, e) for sid, st, n, e in report["per_source"] if st == "skipped"]
    if skipped:
        print(f"Skipped ({len(skipped)}):")
        for sid, e in skipped:
            print(f"  - {sid}: {e}")
    errs = [(sid, e) for sid, st, n, e in report["per_source"] if st == "error"]
    if errs:
        print(f"Errors ({len(errs)}):")
        for sid, e in errs:
            print(f"  - {sid}: {e}")


def cmd_collect(cfg, args):
    print("== signaldesk engine: collect ==")
    report = run_collect(cfg, verbose=True)
    _summary(report)
    return report


def cmd_digest(cfg, args):
    print("== signaldesk engine: digest ==")
    store = Store(cfg.db_path)
    since = now_utc() - timedelta(hours=args.hours)
    path = build_digest(cfg, store, since)
    store.close()
    if path is None:
        print(f"No items fetched in the last {args.hours}h — no digest written.")
    else:
        print(f"Digest written: {path}")
        n = regenerate_index(cfg)
        print(f"INDEX.md regenerated ({n} digest(s)).")
    return path


def cmd_run(cfg, args):
    print("== signaldesk engine: run (collect + digest) ==")
    store = Store(cfg.db_path)
    report = run_collect(cfg, store=store, verbose=True)
    _summary(report)
    path = build_digest(cfg, store, report["run_started"])
    store.close()
    print()
    if path is None:
        print("No new items this run — no digest written.")
    else:
        print(f"Digest written: {path}")
    return path


def cmd_prune(cfg, args):
    print("== signaldesk engine: prune ==")
    store = Store(cfg.db_path)
    n = store.prune_items(args.days)
    store.close()
    print(f"Pruned {n} item(s) older than {args.days} days.")
    return n


def cmd_doctor(cfg, args):
    return doctor_mod.run(cfg)


def cmd_site(cfg, args):
    print("== signaldesk engine: site (HTML edition) ==")
    from .site import build_site
    index_path, public_path, excluded = build_site(cfg)
    for p in (index_path, public_path):
        size = os.path.getsize(p)
        print(f"  wrote {p} ({size:,} bytes)")
    if excluded:
        print(f"  public sanitizer excluded {len(excluded)} item(s):")
        for e in excluded:
            print(f"    - {e}")
    else:
        print("  public sanitizer excluded nothing this build.")
    return index_path


def main(argv=None):
    parser = argparse.ArgumentParser(prog="engine", description=__doc__)
    parser.add_argument("--config", help="path to engine.config.json")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("collect", help="fetch sources, store new items")
    d = sub.add_parser("digest", help="build a digest from recent items")
    d.add_argument("--hours", type=int, default=24, help="lookback window (default 24)")
    sub.add_parser("run", help="collect then digest this run's items")
    p = sub.add_parser("prune", help="drop items older than --days (retention)")
    p.add_argument("--days", type=int, default=90, help="retention window (default 90)")
    sub.add_parser("doctor", help="self-check config, registry, DB, connectivity")
    sub.add_parser("site", help="render the HTML edition into signaldesk/site/")

    args = parser.parse_args(argv)

    # doctor validates paths itself (and reports nicely) — don't pre-validate.
    try:
        cfg = load_config(args.config)
        if args.command != "doctor":
            cfg.validate()
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2

    if args.command == "collect":
        cmd_collect(cfg, args)
    elif args.command == "digest":
        cmd_digest(cfg, args)
    elif args.command == "run":
        cmd_run(cfg, args)
    elif args.command == "prune":
        cmd_prune(cfg, args)
    elif args.command == "doctor":
        return cmd_doctor(cfg, args)
    elif args.command == "site":
        cmd_site(cfg, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
