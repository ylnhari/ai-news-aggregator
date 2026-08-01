"""Transport-level regression tests for the two FLAGS.md 2026-07-18 fixes.

  1. gh-releases (rss.py): llama.cpp-style bare per-build tags ("b10056")
     are noise unless the release names a known model/architecture.
  2. hf-new-models (hf.py): a keyword match alone isn't enough — an upload
     also needs a known-lab namespace or real downloads/likes traction.

No network: engine.transports.http.get_bytes / get_json are monkeypatched.
Stdlib unittest (pytest picks these up too), matching test_engine_core.py.
"""

import unittest

from engine.registry import Source
from engine.transports import hf, rss
from engine.transports import http as transports_http


ATOM_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
{entries}
</feed>
"""

ENTRY_TEMPLATE = """
  <entry>
    <title>{title}</title>
    <link href="{link}"/>
    <summary>{summary}</summary>
    <published>2026-07-18T00:00:00Z</published>
  </entry>
"""


def _atom_feed(entries):
    body = "".join(
        ENTRY_TEMPLATE.format(title=t, link=l, summary=s) for t, l, s in entries
    )
    return ATOM_TEMPLATE.format(entries=body).encode("utf-8")


class RssCfgStub:
    hf_keywords = []
    release_tag_filter_enabled = True
    release_tag_noise_regex = r"^[a-zA-Z]\d{3,}$"
    release_notable_model_keywords = ["qwen", "llama", "deepseek"]


def _gh_source(repos=("ggml-org/llama.cpp",)):
    return Source({
        "id": "gh-releases", "tier": 1, "trust": 1.0,
        "transport": "rss", "handler": "rss", "enabled": True,
        "repos": list(repos),
        "repo_feed_template": "https://github.com/{repo}/releases.atom",
        "beats": ["inference-serving"],
    })


class BuildTagFilterTests(unittest.TestCase):
    def setUp(self):
        self._orig_get_bytes = transports_http.get_bytes

    def tearDown(self):
        transports_http.get_bytes = self._orig_get_bytes

    def _fetch(self, entries, cfg=RssCfgStub, source=None):
        transports_http.get_bytes = lambda url, **kw: _atom_feed(entries)
        return rss.fetch(source or _gh_source(), None, cfg)

    def test_bare_build_tag_suppressed(self):
        items = self._fetch([
            ("b10056", "https://github.com/ggml-org/llama.cpp/releases/tag/b10056",
             "OpenCL ABS op, backend/CI maintenance."),
        ])
        self.assertEqual(items, [], "bare build tag with no named model must be dropped")

    def test_build_tag_naming_model_is_kept(self):
        items = self._fetch([
            ("b10057", "https://github.com/ggml-org/llama.cpp/releases/tag/b10057",
             "Adds native support for the Qwen3-Next architecture."),
        ])
        self.assertEqual(len(items), 1, "a build tag naming a model must survive")
        self.assertEqual(items[0]["title"], "b10057")

    def test_real_release_title_untouched(self):
        items = self._fetch([
            ("Release v5.14.0",
             "https://github.com/huggingface/transformers/releases/tag/v5.14.0",
             "Routine maintenance release."),
        ])
        self.assertEqual(len(items), 1, "titles that aren't bare build tags never match the regex")

    def test_non_repo_feed_is_never_filtered(self):
        # A plain (non repos:) RSS/Atom feed never carries extra["repo"], so
        # even a title that happens to match the noise regex must survive —
        # the filter is scoped to repo-expanded GH release feeds only.
        plain_source = Source({
            "id": "some-blog", "tier": 2, "trust": 0.8,
            "transport": "rss", "handler": "rss", "enabled": True,
            "url": "https://example.com/blog/feed.xml",
            "beats": [],
        })
        items = self._fetch(
            [("b10056", "https://example.com/blog/b10056", "unrelated blog post")],
            source=plain_source,
        )
        self.assertEqual(len(items), 1, "non-repo feeds are out of scope for this filter")

    def test_filter_can_be_disabled_via_config(self):
        class Disabled(RssCfgStub):
            release_tag_filter_enabled = False
        items = self._fetch(
            [("b10056", "https://github.com/ggml-org/llama.cpp/releases/tag/b10056",
              "no model named here")],
            cfg=Disabled,
        )
        self.assertEqual(len(items), 1, "release_tag_filter_enabled=False must be a full bypass")

    def test_mixed_batch_only_suppresses_bare_tags(self):
        items = self._fetch([
            ("b10056", "https://github.com/ggml-org/llama.cpp/releases/tag/b10056",
             "backend maintenance, no model named"),
            ("b10057", "https://github.com/ggml-org/llama.cpp/releases/tag/b10057",
             "Adds Qwen3-Next support"),
            ("Release v5.14.0",
             "https://github.com/ggml-org/llama.cpp/releases/tag/v5.14.0",
             "semantic version release"),
        ])
        titles = {it["title"] for it in items}
        self.assertEqual(titles, {"b10057", "Release v5.14.0"})


class RssWatermarkTests(unittest.TestCase):
    """Date-only pubDates (midnight stamps) vs the daily watermark.

    The HF blog stamps some posts 00:00:00: a post actually published mid-day
    then sits BEFORE the next run's ~03:38Z watermark and is dropped forever
    (missed The Stack v3 + the HF intrusion timeline; distill 2026-08-01).
    A midnight stamp must count as publishable until the end of its day.
    """

    def setUp(self):
        self._orig_get_bytes = transports_http.get_bytes

    def tearDown(self):
        transports_http.get_bytes = self._orig_get_bytes

    def _plain_source(self):
        return Source({
            "id": "hf-blog", "tier": 2, "trust": 0.9,
            "transport": "rss", "handler": "rss", "enabled": True,
            "url": "https://example.com/feed.xml",
            "beats": ["open-weights"],
        })

    def _rss_feed(self, pubdate):
        xml = f"""<rss version="2.0"><channel>
          <item><title>The Stack v3</title>
            <link>https://example.com/stack-v3</link>
            <description>open code dataset</description>
            <pubDate>{pubdate}</pubDate></item>
        </channel></rss>"""
        return xml.encode("utf-8")

    def _fetch(self, pubdate, since):
        transports_http.get_bytes = lambda url, **kw: self._rss_feed(pubdate)
        return rss.fetch(self._plain_source(), since, RssCfgStub)

    def test_midnight_stamp_survives_same_day_watermark(self):
        from datetime import datetime, timezone
        since = datetime(2026, 7, 27, 3, 38, tzinfo=timezone.utc)
        items = self._fetch("Mon, 27 Jul 2026 00:00:00 GMT", since)
        self.assertEqual(len(items), 1,
                         "midnight-stamped same-day post must not be dropped")

    def test_midnight_stamp_still_expires_after_its_day(self):
        from datetime import datetime, timezone
        since = datetime(2026, 7, 29, 3, 38, tzinfo=timezone.utc)
        items = self._fetch("Mon, 27 Jul 2026 00:00:00 GMT", since)
        self.assertEqual(items, [], "two-day-old post must still be filtered")

    def test_real_timestamps_unaffected(self):
        from datetime import datetime, timezone
        since = datetime(2026, 7, 27, 3, 38, tzinfo=timezone.utc)
        kept = self._fetch("Mon, 27 Jul 2026 09:30:00 GMT", since)
        dropped = self._fetch("Mon, 27 Jul 2026 01:30:00 GMT", since)
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, [],
                         "a real pre-watermark timestamp must still filter")


class HfCfgStub:
    hf_keywords = ["qwen", "llama"]
    hf_known_labs = ["qwen", "meta-llama"]
    hf_min_downloads = 100
    hf_min_likes = 5


def _hf_source():
    return Source({
        "id": "hf-new-models", "tier": 1, "trust": 0.9,
        "transport": "api", "handler": "hf", "enabled": True,
        "url": "https://huggingface.co/api/models?sort=createdAt&direction=-1&limit=50",
        "beats": ["open-weights", "fine-tuning"],
    })


def _model(model_id, downloads=0, likes=0, tags=None, pipeline_tag="text-generation"):
    return {
        "id": model_id,
        "pipeline_tag": pipeline_tag,
        "tags": tags or [],
        "downloads": downloads,
        "likes": likes,
        "createdAt": "2026-07-18T00:00:00.000Z",
    }


class HfQualityGateTests(unittest.TestCase):
    def setUp(self):
        self._orig_get_json = transports_http.get_json

    def tearDown(self):
        transports_http.get_json = self._orig_get_json

    def _fetch(self, models, cfg=HfCfgStub):
        transports_http.get_json = lambda url: models
        return hf.fetch(_hf_source(), None, cfg)

    def test_personal_finetune_noise_dropped(self):
        # keyword-matches "qwen" (as most personal fine-tunes do) but has no
        # traction and isn't in a known-lab namespace — this is the exact
        # 14/14, 32/32, 8/8 noise pattern from FLAGS.md.
        items = self._fetch([_model("randomuser/qwen2-0.5b-grpo-test",
                                     downloads=0, likes=0, tags=["qwen"])])
        self.assertEqual(items, [])

    def test_known_lab_upload_kept_even_at_zero_traction(self):
        items = self._fetch([_model("Qwen/Qwen3-72B-Instruct",
                                     downloads=0, likes=0, tags=["qwen"])])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["extra"]["hf_id"], "Qwen/Qwen3-72B-Instruct")

    def test_non_lab_upload_with_traction_kept(self):
        items = self._fetch([_model("randomuser/llama-community-finetune",
                                     downloads=500, likes=50, tags=["llama"])])
        self.assertEqual(len(items), 1)

    def test_non_lab_upload_below_threshold_dropped(self):
        items = self._fetch([_model("randomuser/llama-community-finetune",
                                     downloads=10, likes=1, tags=["llama"])])
        self.assertEqual(items, [])

    def test_keyword_filter_still_applies_first(self):
        # not matching hf_keywords at all (e.g. unrelated domain) is still
        # dropped regardless of traction/lab — the quality gate is additive.
        items = self._fetch([_model("bigorg/unrelated-vision-model",
                                     downloads=10_000, likes=1_000, tags=["vision"])])
        self.assertEqual(items, [])


if __name__ == "__main__":
    unittest.main()
