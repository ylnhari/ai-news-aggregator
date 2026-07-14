"""Engine self-check — `python -m engine doctor`.

Validates the wiring before a scheduled run is trusted:
  1. config paths exist (signaldesk_dir, registry, db parent)
  2. registry JSON parses
  3. DB is reachable (opens / creates the SQLite file + schema)
  4. one dry HTTP HEAD to a couple of enabled sources (connectivity smoke test)
  5. a per-source table: enabled / disabled / pending-creds

Exit 0 when the core checks (1-3) pass; HEAD failures are reported but NON-fatal
(a flaky endpoint must not fail the doctor). Config/registry/DB failures exit 1.
"""

import urllib.error
import urllib.request

from .config import ConfigError
from .registry import load_registry
from .store import Store
from .transports import HANDLERS, CREDS_CHECK
from .transports.http import USER_AGENT

_HEAD_TIMEOUT = 10


def _ok(msg):
    print(f"  [ok]   {msg}")


def _warn(msg):
    print(f"  [warn] {msg}")


def _fail(msg):
    print(f"  [FAIL] {msg}")


def _head(url: str) -> str:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_HEAD_TIMEOUT) as resp:
            return f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"  # a response IS reachability (403/405 still "up")
    except Exception as e:  # noqa: BLE001
        return f"unreachable: {type(e).__name__}: {e}"[:120]


def _source_status(src) -> str:
    if not src.enabled or src.handler == "unsupported":
        return "disabled"
    if src.handler in CREDS_CHECK and not CREDS_CHECK[src.handler]():
        return "pending-creds"
    if src.handler not in HANDLERS:
        return f"no-handler({src.handler})"
    return "enabled"


def run(cfg) -> int:
    print("== signaldesk engine: doctor ==")
    failed = False

    # 1 · config paths -------------------------------------------------------
    print("\nConfig paths:")
    try:
        cfg.validate()
        _ok(f"signaldesk_dir: {cfg.signaldesk_dir}")
        _ok(f"registry: {cfg.registry_path}")
        _ok(f"db_path: {cfg.db_path}")
    except ConfigError as e:
        _fail(str(e))
        return 1  # nothing else is meaningful without valid paths

    # 2 · registry parse -----------------------------------------------------
    print("\nRegistry:")
    try:
        sources = load_registry(cfg.registry_path)
        _ok(f"parsed {len(sources)} source(s)")
    except Exception as e:  # noqa: BLE001
        _fail(f"registry parse failed: {type(e).__name__}: {e}")
        return 1

    # 3 · DB reachable -------------------------------------------------------
    print("\nDatabase:")
    try:
        store = Store(cfg.db_path)
        n = store.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        _ok(f"opened SQLite, schema present ({n} item row(s))")
        store.close()
    except Exception as e:  # noqa: BLE001
        _fail(f"DB error: {type(e).__name__}: {e}")
        failed = True

    # 5 · per-source table (computed before HEADs so we can pick live targets)
    rows = [(s.id, s.transport or "-", s.handler or "-", _source_status(s), s) for s in sources]
    enabled_ct = sum(1 for r in rows if r[3] == "enabled")
    pending_ct = sum(1 for r in rows if r[3] == "pending-creds")
    disabled_ct = sum(1 for r in rows if r[3] == "disabled")

    # 4 · dry HTTP HEAD to a couple of enabled, url-bearing sources ----------
    print("\nConnectivity (dry HEAD, non-fatal):")
    live_targets = [s for (_id, _t, _h, st, s) in rows
                    if st == "enabled" and (s.url or "").startswith("http")][:2]
    if not live_targets:
        _warn("no enabled url-bearing source to probe")
    for s in live_targets:
        result = _head(s.url)
        (_ok if result.startswith("HTTP") else _warn)(f"{s.id:22s} {s.url} -> {result}")

    # 5 · print the table ----------------------------------------------------
    print("\nSources:")
    print(f"  {'id':24s} {'transport':13s} {'handler':12s} status")
    print(f"  {'-'*24} {'-'*13} {'-'*12} {'-'*13}")
    for sid, transport, handler, status, _s in rows:
        print(f"  {sid:24s} {transport:13s} {handler:12s} {status}")
    print(f"\n  {enabled_ct} enabled · {pending_ct} pending-creds · {disabled_ct} disabled")

    print()
    if failed:
        print("doctor: FAIL — see [FAIL] lines above.")
        return 1
    print("doctor: OK — config, registry and DB all healthy.")
    return 0
