"""SQLite store: items, per-source run watermarks, and html-diff snapshots.

Identity: item id = sha1(canonical url). The watermark model means every
source fetches "since its last successful run" (falling back to a window on
first run), so missed days self-heal on the next run — never "last 24h".
"""

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone


def sha1_id(canonical_url: str) -> str:
    return hashlib.sha1(canonical_url.encode("utf-8")).hexdigest()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(s: str):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_runs (
    source_id        TEXT PRIMARY KEY,
    last_success_utc TEXT,
    last_status      TEXT,
    last_error       TEXT
);
CREATE TABLE IF NOT EXISTS items (
    id            TEXT PRIMARY KEY,
    source_id     TEXT NOT NULL,
    url           TEXT,
    title         TEXT,
    published_utc TEXT,
    fetched_utc   TEXT,
    excerpt       TEXT,
    beats         TEXT,
    extra         TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    source_id       TEXT NOT NULL,
    url             TEXT,
    normalized_hash TEXT,
    extracted       TEXT,
    taken_utc       TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_source   ON items(source_id);
CREATE INDEX IF NOT EXISTS idx_items_fetched  ON items(fetched_utc);
CREATE INDEX IF NOT EXISTS idx_snap_source    ON snapshots(source_id);
"""


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path
        import os
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # --- watermarks ---------------------------------------------------------
    def watermark(self, source_id: str, window_hours_first_run: int) -> datetime:
        """Return the 'fetch since' cutoff for a source: its last success, or
        now - first-run window if it has never succeeded."""
        row = self.conn.execute(
            "SELECT last_success_utc FROM source_runs WHERE source_id=?",
            (source_id,),
        ).fetchone()
        if row and row["last_success_utc"]:
            dt = parse_iso(row["last_success_utc"])
            if dt:
                return dt
        return now_utc() - timedelta(hours=window_hours_first_run)

    def record_run(self, source_id: str, status: str, error: str = "",
                   success_time: datetime = None):
        """Record a run outcome. On success, advance the watermark."""
        existing = self.conn.execute(
            "SELECT last_success_utc FROM source_runs WHERE source_id=?",
            (source_id,),
        ).fetchone()
        prev_success = existing["last_success_utc"] if existing else None
        if status == "ok":
            last_success = iso(success_time or now_utc())
        else:
            last_success = prev_success  # a failure does NOT advance the watermark
        self.conn.execute(
            """INSERT INTO source_runs(source_id, last_success_utc, last_status, last_error)
               VALUES(?,?,?,?)
               ON CONFLICT(source_id) DO UPDATE SET
                 last_success_utc=excluded.last_success_utc,
                 last_status=excluded.last_status,
                 last_error=excluded.last_error""",
            (source_id, last_success, status, error or ""),
        )
        self.conn.commit()

    # --- items --------------------------------------------------------------
    def upsert_item(self, item: dict) -> bool:
        """Insert an item if new. Returns True if it was new (INSERTed).

        item keys: url, title, published_utc, excerpt, beats(list), extra(dict),
        source_id. id is derived from the canonical url.
        """
        canonical = item["url"]
        item_id = sha1_id(canonical)
        exists = self.conn.execute(
            "SELECT 1 FROM items WHERE id=?", (item_id,)
        ).fetchone()
        if exists:
            return False
        self.conn.execute(
            """INSERT INTO items(id, source_id, url, title, published_utc,
                                 fetched_utc, excerpt, beats, extra)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                item_id,
                item["source_id"],
                canonical,
                item.get("title", ""),
                item.get("published_utc") or "",
                iso(now_utc()),
                item.get("excerpt", ""),
                json.dumps(item.get("beats", [])),
                json.dumps(item.get("extra", {})),
            ),
        )
        return True

    def commit(self):
        self.conn.commit()

    def items_since(self, since: datetime):
        """All items fetched at/after `since`, newest first — the digest window."""
        rows = self.conn.execute(
            "SELECT * FROM items WHERE fetched_utc >= ? ORDER BY fetched_utc DESC",
            (iso(since),),
        ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def items_fetched_between(self, start: datetime, end: datetime):
        rows = self.conn.execute(
            "SELECT * FROM items WHERE fetched_utc >= ? AND fetched_utc <= ? "
            "ORDER BY fetched_utc DESC",
            (iso(start), iso(end)),
        ).fetchall()
        return [self._row_to_item(r) for r in rows]

    @staticmethod
    def _row_to_item(r) -> dict:
        return {
            "id": r["id"],
            "source_id": r["source_id"],
            "url": r["url"],
            "title": r["title"],
            "published_utc": r["published_utc"],
            "fetched_utc": r["fetched_utc"],
            "excerpt": r["excerpt"],
            "beats": json.loads(r["beats"] or "[]"),
            "extra": json.loads(r["extra"] or "{}"),
        }

    # --- snapshots (html-diff / greenhouse) ---------------------------------
    def last_snapshot(self, source_id: str):
        row = self.conn.execute(
            "SELECT * FROM snapshots WHERE source_id=? ORDER BY taken_utc DESC LIMIT 1",
            (source_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "source_id": row["source_id"],
            "url": row["url"],
            "normalized_hash": row["normalized_hash"],
            "extracted": json.loads(row["extracted"] or "[]"),
            "taken_utc": row["taken_utc"],
        }

    def save_snapshot(self, source_id: str, url: str, normalized_hash: str,
                      extracted, keep_history: bool = False):
        if not keep_history:
            self.conn.execute("DELETE FROM snapshots WHERE source_id=?", (source_id,))
        self.conn.execute(
            """INSERT INTO snapshots(source_id, url, normalized_hash, extracted, taken_utc)
               VALUES(?,?,?,?,?)""",
            (source_id, url, normalized_hash, json.dumps(extracted), iso(now_utc())),
        )
        self.conn.commit()

    # --- retention ----------------------------------------------------------
    def prune_items(self, older_than_days: int = 90) -> int:
        cutoff = iso(now_utc() - timedelta(days=older_than_days))
        cur = self.conn.execute("DELETE FROM items WHERE fetched_utc < ?", (cutoff,))
        self.conn.commit()
        return cur.rowcount
