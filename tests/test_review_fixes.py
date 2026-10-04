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
