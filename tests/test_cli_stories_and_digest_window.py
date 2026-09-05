"""Regression tests for two FLAGS.md 2026-08-25/08-27 bugs:

  1. `engine stories` crashed with UnicodeEncodeError on a Windows cp1252
     console whenever a stored item title contained a character outside
     that codepage (a non-breaking hyphen, then separately an emoji).
  2. `cmd_run` anchored the digest window on "this invocation's own start
     time" instead of "since the last digest was written" -- a retry of
     `engine run` after a partial failure silently dropped the first pass's
     items from the digest, even though nothing was lost from the store.

No network, no LLM: temp SQLite + temp workspace per test, same pattern as
test_engine_core.py.
"""

import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone

from engine.store import Store
from engine import digest
from engine.digest import build_digest
from engine.config import Config
from engine.__main__ import cmd_stories

EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


def item(url, title, source="test"):
    return {"url": url, "title": title, "source_id": source,
            "published_utc": "", "excerpt": "", "beats": [], "extra": {}}


class CfgStub:
    beat_weights = {}
    default_beat_weight = 0.5
    hn_points_norm = 200


class _DbPathCfg:
    """cmd_stories only ever touches cfg.db_path."""
    def __init__(self, db_path):
        self.db_path = db_path


class _Args:
    def __init__(self, id_=None, days=30):
        self.id = id_
        self.days = days


class StoriesUnicodeTests(unittest.TestCase):
    """FLAGS.md 2026-08-25 / 2026-08-27: `stories --id` and `stories --days`
    both crashed on a stored title with a character outside the console's
    codepage. Simulate a cp1252 Windows console by wrapping a BytesIO with
    encoding="cp1252", errors="strict" -- that's what raises before the fix."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "t.db")
        self.store = Store(self.db_path)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run_with_cp1252_stdout(self, args):
        buf = io.BytesIO()
        wrapper = io.TextIOWrapper(buf, encoding="cp1252", errors="strict")
        old_stdout = sys.stdout
        sys.stdout = wrapper
        try:
            rc = cmd_stories(_DbPathCfg(self.db_path), args)
        finally:
            try:
                wrapper.flush()
            except UnicodeEncodeError:
                raise
            finally:
                sys.stdout = old_stdout
        return rc

    def test_stories_by_id_survives_non_cp1252_title(self):
        # non-breaking hyphen (U+2011) + fire emoji: neither is in cp1252.
        bad_title = "GPT‑X launches \U0001F525 today"
        self.store.upsert_item(item("https://a.com/1", bad_title))
        self.store.commit()
        self.store.create_story("evt-test-unicode", bad_title, "gptx")
        self.store.touch_story("evt-test-unicode", bad_title, "gptx", 1)
        self.store.link_items_to_story(
            [self.store.items_since(EPOCH)[0]["id"]], "evt-test-unicode")
        self.store.commit()

        rc = self._run_with_cp1252_stdout(_Args(id_="evt-test-unicode"))
        self.assertEqual(rc, 0)

    def test_stories_by_days_survives_non_cp1252_state(self):
        bad_title = "GLM‑X ships \U0001F525"
        self.store.create_story("evt-test-unicode-2", bad_title, "glmx",
                                state=bad_title)
        self.store.commit()

        rc = self._run_with_cp1252_stdout(_Args(id_=None, days=30))
        self.assertEqual(rc, 0)


class DigestWindowRetryTests(unittest.TestCase):
    """FLAGS.md 2026-08-27: a retried `engine run` must not lose the first
    pass's items just because the second invocation started later."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        ws = os.path.join(self.tmp, "ws")
        os.makedirs(os.path.join(ws, "registry"))
        with open(os.path.join(ws, "registry", "sources.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"sources": [
                {"id": "test", "tier": 1, "trust": 1.0, "transport": "rss",
                 "handler": "rss", "enabled": True,
                 "url": "https://example.com/feed", "beats": []}]}, f)
        self.cfg = Config({"signaldesk_dir": ws,
                           "db_path": os.path.join(ws, "t.db")}, "test")
        self.store = Store(self.cfg.db_path)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_last_digest_before_ignores_todays_own_in_progress_file(self):
        os.makedirs(self.cfg.digests_dir, exist_ok=True)
        prior_path = os.path.join(self.cfg.digests_dir, "2000-01-01.md")
        with open(prior_path, "w", encoding="utf-8") as f:
            f.write("prior day digest\n")
        old_time = time.time() - 86400
        os.utime(prior_path, (old_time, old_time))

        # Today's own digest, already written by "run 1" moments ago.
        today_path = os.path.join(self.cfg.digests_dir, "2000-01-02.md")
        with open(today_path, "w", encoding="utf-8") as f:
            f.write("run 1's in-progress digest\n")

        anchor = digest.last_digest_before(self.cfg, "2000-01-02")
        self.assertIsNotNone(anchor)
        self.assertAlmostEqual(anchor.timestamp(), old_time, delta=2,
                                msg="must anchor on the prior day's digest, "
                                    "never on today's own in-progress file")

    def test_first_ever_digest_has_no_anchor(self):
        os.makedirs(self.cfg.digests_dir, exist_ok=True)
        self.assertIsNone(digest.last_digest_before(self.cfg, "2000-01-02"))

    def test_retry_keeps_first_passs_items_in_the_rebuilt_digest(self):
        os.makedirs(self.cfg.digests_dir, exist_ok=True)
        prior_path = os.path.join(self.cfg.digests_dir, "2000-01-01.md")
        with open(prior_path, "w", encoding="utf-8") as f:
            f.write("prior day digest\n")
        old_time = time.time() - 86400
        os.utime(prior_path, (old_time, old_time))
        anchor = digest.last_digest_before(self.cfg, "2000-01-02")

        # "run 1": collects item A, builds today's digest anchored on the
        # last (prior-day) digest -- exactly what cmd_run now does.
        self.store.upsert_item(item("https://a.com/1", "First pass item"))
        self.store.commit()
        path1 = build_digest(self.cfg, self.store, anchor)
        self.assertIsNotNone(path1)
        with open(path1, encoding="utf-8") as f:
            self.assertIn("First pass item", f.read())

        # "run 2": retries later the SAME day after collecting a stray item.
        # A naive since=this-run's-own-start would show only item B (the
        # original bug -- items=1). Anchoring on last_digest_before(today)
        # must resolve to the SAME prior-day anchor (today's own file, from
        # run 1, is correctly excluded) so both items survive.
        self.store.upsert_item(item("https://b.com/2", "Retry pass item"))
        self.store.commit()
        today_str = os.path.basename(path1)[:-3]
        anchor2 = digest.last_digest_before(self.cfg, today_str)
        self.assertEqual(anchor2, anchor)

        path2 = build_digest(self.cfg, self.store, anchor2)
        self.assertEqual(path2, path1)
        with open(path2, encoding="utf-8") as f:
            text2 = f.read()
        self.assertIn("First pass item", text2,
                      "retry must not drop the first pass's items")
        self.assertIn("Retry pass item", text2)


if __name__ == "__main__":
    unittest.main()
