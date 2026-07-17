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
CREATE TABLE IF NOT EXISTS stories (
    id            TEXT PRIMARY KEY,   -- evt-YYYYMMDD-slug
    title         TEXT,               -- canonical headline (refreshed on updates)
    fingerprint   TEXT,               -- space-joined salient title tokens (grows)
    state         TEXT,               -- one-line current state (LLM-refinable)
    opened_utc    TEXT,
    last_seen_utc TEXT,
    item_count    INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_source   ON items(source_id);
CREATE INDEX IF NOT EXISTS idx_items_fetched  ON items(fetched_utc);
CREATE INDEX IF NOT EXISTS idx_snap_source    ON snapshots(source_id);
CREATE INDEX IF NOT EXISTS idx_stories_seen   ON stories(last_seen_utc);
"""

# Columns added after first release — applied best-effort on open (SQLite has
# no IF NOT EXISTS for columns).
_MIGRATIONS = [
    "ALTER TABLE items ADD COLUMN story_id TEXT",
]


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path
        import os
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        for mig in _MIGRATIONS:
            try:
                self.conn.execute(mig)
            except sqlite3.OperationalError:
                pass  # column already exists
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
        source_id. id is derived from the canonical url (tracking params,
        fragments, trailing slashes stripped — so utm-tagged copies dedup).
        """
        from .util import canonical_url
        canonical = canonical_url(item["url"])
        item_id = sha1_id(canonical)
        exists = self.conn.execute(
            "SELECT source_id, extra FROM items WHERE id=?", (item_id,)
        ).fetchone()
        if exists:
            # Same URL arriving via a second source (e.g. the vendor's own
            # feed after an aggregator already caught it): keep the row, but
            # record the corroborating source in extra.also_seen — otherwise
            # the duplicate is silently lost and a healthy primary source
            # looks dead in mesh-health (the Kimi K3 launch-day false alarm).
            new_sid = item["source_id"]
            extra = json.loads(exists["extra"] or "{}")
            also = extra.get("also_seen", [])
            seen = {exists["source_id"]} | {a.get("source_id") for a in also}
            if new_sid not in seen:
                also.append({"source_id": new_sid,
                             "fetched_utc": iso(now_utc())})
                extra["also_seen"] = also
                self.conn.execute("UPDATE items SET extra=? WHERE id=?",
                                  (json.dumps(extra), item_id))
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
            "story_id": r["story_id"] if "story_id" in r.keys() else None,
        }

    # --- stories (cross-day event tracking; DESIGN.md L1 "EVENT", minimal) ---
    def open_stories(self, days: int = 10):
        """Stories seen within the last `days` — the active matching window
        (research: story clusters cap out around ~10 days)."""
        cutoff = iso(now_utc() - timedelta(days=days))
        rows = self.conn.execute(
            "SELECT * FROM stories WHERE last_seen_utc >= ? "
            "ORDER BY last_seen_utc DESC", (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]

    def create_story(self, story_id: str, title: str, fingerprint: str,
                     state: str = ""):
        now = iso(now_utc())
        self.conn.execute(
            """INSERT OR IGNORE INTO stories
               (id, title, fingerprint, state, opened_utc, last_seen_utc, item_count)
               VALUES(?,?,?,?,?,?,0)""",
            (story_id, title, fingerprint, state or title, now, now),
        )

    def touch_story(self, story_id: str, latest_title: str,
                    new_tokens: str, added_items: int):
        """An update landed: refresh last_seen, grow the fingerprint, bump count.
        The title/state stay owned by the judgment pass (set_story_state)."""
        row = self.conn.execute(
            "SELECT fingerprint, item_count FROM stories WHERE id=?",
            (story_id,),
        ).fetchone()
        if not row:
            return
        fp = set((row["fingerprint"] or "").split()) | set(new_tokens.split())
        self.conn.execute(
            "UPDATE stories SET last_seen_utc=?, fingerprint=?, item_count=? "
            "WHERE id=?",
            (iso(now_utc()), " ".join(sorted(fp)[:40]),
             (row["item_count"] or 0) + added_items, story_id),
        )

    def set_story_state(self, story_id: str, state: str) -> bool:
        cur = self.conn.execute(
            "UPDATE stories SET state=? WHERE id=?", (state, story_id))
        self.conn.commit()
        return cur.rowcount > 0

    def link_items_to_story(self, item_ids, story_id: str):
        self.conn.executemany(
            "UPDATE items SET story_id=? WHERE id=?",
            [(story_id, iid) for iid in item_ids],
        )

    def story_items(self, story_id: str):
        rows = self.conn.execute(
            "SELECT * FROM items WHERE story_id=? ORDER BY fetched_utc",
            (story_id,),
        ).fetchall()
        return [self._row_to_item(r) for r in rows]

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
