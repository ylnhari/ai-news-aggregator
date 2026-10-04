"""Regression tests for the 2026-10-04 review fixes (signaldesk FLAGS/PENDING B4)."""

import os
import shutil
import tempfile
import unittest

from engine import stories, rank
from engine.store import Store
from engine.util import strip_html
from engine.site import _render_story


class StripHtmlTests(unittest.TestCase):
    def test_entity_escaped_markup_is_stripped(self):
        raw = 'tech report: &lt;a href=&quot;https:&#x2F;&#x2F;a.com&#x2F;r.pdf&quot;&gt;link&lt;&#x2F;a&gt;'
        out = strip_html(raw)
        self.assertNotIn("<", out)
        self.assertNotIn("&", out)
        self.assertIn("link", out)

    def test_plain_entities_still_decoded(self):
        self.assertEqual(strip_html("a &amp; b"), "a & b")


class PublicGistTests(unittest.TestCase):
    def _story(self, gist):
        return {"headline": "H", "gist": gist, "sources": []}

    def test_placeholder_gist_hidden_on_public_only(self):
        s = self._story(["(no summary captured — open the source.)"])
        self.assertNotIn("no summary captured", _render_story(s, public=True))
        self.assertIn("no summary captured", _render_story(s, public=False))

    def test_collector_internal_excerpt_hidden_on_public(self):
        s = self._story(["New link on kimi-blog"])
        self.assertNotIn("kimi-blog", _render_story(s, public=True))


class UrlMergeTests(unittest.TestCase):
    def test_items_sharing_a_link_become_one_story(self):
        tmp = tempfile.mkdtemp()
        store = Store(os.path.join(tmp, "t.db"))
        try:
            def grp(headline, url, excerpt=""):
                return {"headline": headline, "items": [
                    {"id": url, "url": url, "title": headline, "excerpt": excerpt}]}
            a = grp("Kolibri: A Sovereign Open-Weight Model",
                    "https://aleph-alpha.com/en/blog/kolibri-landed")
            b = grp("Aleph Alpha explainer on how the German model works",
                    "https://tej.as/blog/aleph-alpha-kolibri",
                    "see &lt;a href=&quot;https:&#x2F;&#x2F;www.aleph-alpha.com&#x2F;en&#x2F;blog&#x2F;kolibri-landed&#x2F;&quot;&gt;post&lt;&#x2F;a&gt;")
            for g in (a, b):
                for it in g["items"]:
                    store.upsert_item({**it, "source_id": "t", "published_utc": "",
                                       "beats": [], "extra": {}})
            store.commit()
            for g in (a, b):  # items need store ids
                g["items"] = [dict(i, id=r["id"]) for i, r in
                              zip(g["items"], [x for x in store.items_since(
                                  __import__("datetime").datetime(2000, 1, 1, tzinfo=__import__("datetime").timezone.utc))
                                  if x["url"] == g["items"][0]["url"]])]
            stories.assign_stories(store, [a, b], top_n=7, date_str="2026-10-04")
            self.assertEqual(a["story"]["id"], b["story"]["id"])
        finally:
            store.close()
            shutil.rmtree(tmp, ignore_errors=True)


class StoplistTests(unittest.TestCase):
    def test_generic_words_do_not_anchor(self):
        self.assertEqual(rank._anchors("llm 0.35 released"), set())


if __name__ == "__main__":
    unittest.main()


class TruncatedTagTests(unittest.TestCase):
    def test_tag_cut_off_by_excerpt_cap_is_dropped(self):
        out = strip_html('demos: &lt;a href=&quot;https://y.com/w?v=1&quot;…')
        self.assertEqual(out, "demos:")


class OpenRouterDiffTests(unittest.TestCase):
    def test_new_model_and_price_move(self):
        from engine.transports import openrouter as orr
        prev = {"a/x": {"id": "a/x", "name": "X", "prompt": 1.0, "completion": 4.0},
                "a/y": {"id": "a/y", "name": "Y", "prompt": 2.0, "completion": 8.0}}
        cur = orr._current({"data": [
            {"id": "a/x", "name": "X", "pricing": {"prompt": "0.0000005", "completion": "0.000004"}},
            {"id": "a/y", "name": "Y", "pricing": {"prompt": "0.0000020", "completion": "0.000008"}},
            {"id": "b/z", "name": "Z", "pricing": {"prompt": "0.000001", "completion": "0.000002"}}]})
        new, changed = orr.diff(prev, cur)
        self.assertEqual([m["id"] for m in new], ["b/z"])
        self.assertEqual([m["id"] for _, m in changed], ["a/x"])


class PageDiffTests(unittest.TestCase):
    def test_lines_and_diff(self):
        from engine.transports import pagediff as pd
        html_old = "<html><body><script>var x=1;</script><table><tr><td>Opus</td><td>$5</td></tr></table></body></html>"
        html_new = html_old.replace("$5", "$4") .replace("</table>", "<tr><td>Sonnet</td><td>$2</td></tr></table>")
        old, new = pd._lines(html_old * 1), pd._lines(html_new)
        self.assertNotIn("var x=1;", " ".join(new))
        added, removed = pd.diff_lines(old, new)
        self.assertIn("$4", added)
        self.assertIn("$5", removed)


class BeatDriftTests(unittest.TestCase):
    def test_reports_both_directions(self):
        from engine.doctor import beat_weight_drift
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "i.md")
        open(p, "w").write("| beat | weight | notes |\n|---|---|---|\n| a | 1.0 | x |\n| b | 0.5 | y |\n")
        out = beat_weight_drift(p, {"a": 0.9, "c": 0.3})
        shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(len(out), 3)
