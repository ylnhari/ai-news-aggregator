"""Engine invariants — stdlib unittest (pytest picks these up too).

Covers the promises the README makes:
  1. URL canonicalization dedups tracking-param copies.
  2. Anchor tokens ("Gemma 4" -> gemma4) group cross-source echoes same-day.
  3. The story ledger links a NEW development to yesterday's event (the
     "Gemma 4 released" -> "bug found in Gemma 4" scenario) as an UPDATE.
  4. A judged digest is never overwritten by a re-run (pending-marker guard).
  5. Digest caps bound what an LLM reader sees (go-deeper links per story).

No network, no LLM: temp SQLite + temp workspace per test.
"""

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone

from engine.store import Store
from engine import rank, stories
from engine.digest import build_digest, MAX_DEEPER_LINKS, PENDING_MARKER
from engine.config import Config

EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


def item(url, title, source="test"):
    return {"url": url, "title": title, "source_id": source,
            "published_utc": "", "excerpt": "", "beats": [], "extra": {}}


class CfgStub:
    beat_weights = {}
    default_beat_weight = 0.5
    hn_points_norm = 200


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = Store(os.path.join(self.tmp, "t.db"))

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_tracking_params_dedup(self):
        a = self.store.upsert_item(item("https://x.com/post", "T"))
        b = self.store.upsert_item(item("https://x.com/post?utm_source=hn", "T"))
        c = self.store.upsert_item(item("https://x.com/post/", "T"))
        self.assertEqual((a, b, c), (True, False, False))

    def test_story_two_day_update(self):
        d1 = [item("https://a.com/1", "Gemma 4 released: open weights, 70B flagship"),
              item("https://b.com/2", "Google ships Gemma 4 open-weights model family")]
        for it in d1:
            self.store.upsert_item(it)
        self.store.commit()
        groups = rank.rank_and_group(self.store.items_since(EPOCH),
                                     {"test": 1.0}, CfgStub)
        self.assertEqual(len(groups), 1, "anchor gemma4 should merge echoes")
        stories.assign_stories(self.store, groups, top_n=7,
                               date_str="2026-07-14")

        self.store.upsert_item(item(
            "https://c.com/3",
            "Researchers find quantization regression in Gemma 4 GGUF builds"))
        self.store.commit()
        day2 = [i for i in self.store.items_since(EPOCH)
                if "quantization" in i["title"].lower()]
        groups2 = rank.rank_and_group(day2, {"test": 1.0}, CfgStub)
        stories.assign_stories(self.store, groups2, top_n=7,
                               date_str="2026-07-15")
        self.assertIsNotNone(groups2[0]["story"])
        self.assertEqual(groups2[0]["story"]["status"], "update")
        open_now = self.store.open_stories()
        self.assertEqual(len(open_now), 1)
        self.assertEqual(open_now[0]["item_count"], 3)


class DigestTests(unittest.TestCase):
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

    def test_judged_digest_never_overwritten(self):
        self.store.upsert_item(item("https://a.com/1", "Something happened"))
        self.store.commit()
        path = build_digest(self.cfg, self.store, EPOCH)
        self.assertIsNotNone(path)
        with open(path, encoding="utf-8") as f:
            self.assertIn(PENDING_MARKER, f.read())
        judged = "# judged content\n*judged pass: test, now*\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(judged)
        result = build_digest(self.cfg, self.store, EPOCH)
        self.assertIsNone(result, "guard must refuse to overwrite")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), judged)

    def test_go_deeper_capped(self):
        for i in range(MAX_DEEPER_LINKS + 8):
            self.store.upsert_item(item(
                f"https://s{i}.com/gemma-4-launch",
                "Gemma 4 released and everyone is covering Gemma 4"))
        self.store.commit()
        path = build_digest(self.cfg, self.store, EPOCH)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("more corroborating link", text)
        self.assertLessEqual(text.count("· ["), MAX_DEEPER_LINKS)


if __name__ == "__main__":
    unittest.main()
