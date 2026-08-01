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

    def test_cross_source_dup_records_also_seen(self):
        # the Kimi K3 launch-day case: an aggregator catches a vendor post
        # first; when the vendor's own feed fetches the same URL, the
        # duplicate must be recorded on the item, not silently dropped
        self.assertTrue(self.store.upsert_item(
            item("https://kimi.com/blog/k3", "Kimi K3", source="hn-algolia")))
        self.assertFalse(self.store.upsert_item(
            item("https://kimi.com/blog/k3?utm_source=rss", "Kimi K3",
                 source="kimi-blog")))
        self.store.commit()
        it = self.store.items_since(EPOCH)[0]
        self.assertEqual(it["source_id"], "hn-algolia")
        self.assertEqual([a["source_id"] for a in it["extra"]["also_seen"]],
                         ["kimi-blog"])
        # same source repeating must not grow the list
        self.store.upsert_item(
            item("https://kimi.com/blog/k3", "Kimi K3", source="kimi-blog"))
        self.store.commit()
        it = self.store.items_since(EPOCH)[0]
        self.assertEqual(len(it["extra"]["also_seen"]), 1)

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


class SingleLinkDampTests(unittest.TestCase):
    """Recurring FLAGS 2026-07-28..31: heuristic top-rank kept promoting
    single-link low-trust (HN) hits above tier-1 vendor stories on momentum
    alone. A one-item group from a low-trust source is damped; corroborated
    groups and trusted sources are not."""

    TRUST = {"hn-algolia": 0.7, "vendor-news": 1.0}

    def _hn(self, url, title, points=200):
        it = item(url, title, source="hn-algolia")
        it["extra"] = {"hn_points": points}
        return it

    def test_single_link_hn_hit_is_damped(self):
        groups = rank.rank_and_group(
            [self._hn("https://a.com/1", "Gaming mouse firmware teardown")],
            self.TRUST, CfgStub)
        # base 0.7*0.5 + momentum 1.0 = 1.35, damped by the 0.6 default
        self.assertAlmostEqual(groups[0]["score"], round(1.35 * 0.6, 4))

    def test_vendor_story_now_outranks_single_hn_hit(self):
        class DampCfg(CfgStub):
            beat_weights = {"frontier-releases": 1.0}
        vendor = item("https://vendor.com/launch", "Frontier model launched",
                      source="vendor-news")
        vendor["beats"] = ["frontier-releases"]
        groups = rank.rank_and_group(
            [self._hn("https://a.com/1", "Gaming mouse firmware teardown"),
             vendor],
            self.TRUST, DampCfg)
        # vendor 1.0*1.0 = 1.0 beats the damped HN hit (1.35*0.6 = 0.81);
        # pre-damp the HN hit would have won 1.35 vs 1.0
        self.assertEqual(groups[0]["primary"]["source_id"], "vendor-news")

    def test_corroborated_low_trust_group_not_damped(self):
        groups = rank.rank_and_group(
            [self._hn("https://a.com/1", "Gemma 4 quantization regression found"),
             self._hn("https://b.com/2", "Gemma 4 GGUF quantization regression thread",
                      points=10)],
            self.TRUST, CfgStub)
        self.assertEqual(len(groups), 1, "echoes should group")
        self.assertAlmostEqual(groups[0]["score"], 1.35,
                               msg="multi-item group keeps its full score")

    def test_trusted_single_item_not_damped(self):
        groups = rank.rank_and_group(
            [item("https://vendor.com/launch", "Frontier model launched",
                  source="vendor-news")],
            self.TRUST, CfgStub)
        self.assertAlmostEqual(groups[0]["score"], 0.5)


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
                 "url": "https://example.com/feed", "beats": []},
                {"id": "vendor", "tier": 1, "trust": 1.0, "transport": "rss",
                 "handler": "rss", "enabled": True,
                 "url": "https://vendor.com/feed", "beats": []}]}, f)
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

    def test_dup_only_source_not_reported_zero(self):
        # a source whose only fetch this window was a cross-source duplicate
        # is alive — it must not appear in mesh-health's zero list
        from engine.digest import _mesh_health
        self.store.upsert_item(item("https://a.com/k3", "K3", source="test"))
        self.store.upsert_item(item("https://a.com/k3", "K3", source="vendor"))
        self.store.commit()
        mesh = _mesh_health(self.cfg, self.store,
                            self.store.items_since(EPOCH))
        self.assertNotIn("vendor", mesh["zero"])

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
