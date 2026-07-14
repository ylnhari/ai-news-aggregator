"""CLI: python -m engine {collect|digest|run}

  collect   fetch every enabled source since its watermark; store new items.
  digest    build a Markdown digest from recently-fetched items (--hours window).
  run       collect, then digest this run's new items (the scheduled daily job).
"""

import argparse
import sys
from datetime import timedelta

from .config import load_config, ConfigError, IST
from .store import Store, now_utc
from .collect import collect as run_collect
from .digest import build_digest, regenerate_index


def _summary(report):
    print()
    print(f"Collected: {report['total_new']} new item(s) across "
          f"{report['active_source_count']} active source(s); pruned {report['pruned']}.")
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


def main(argv=None):
    parser = argparse.ArgumentParser(prog="engine", description=__doc__)
    parser.add_argument("--config", help="path to engine.config.json")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("collect", help="fetch sources, store new items")
    d = sub.add_parser("digest", help="build a digest from recent items")
    d.add_argument("--hours", type=int, default=24, help="lookback window (default 24)")
    sub.add_parser("run", help="collect then digest this run's items")

    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
