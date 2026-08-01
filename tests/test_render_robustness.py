"""Renderer robustness — the judge writes rich Markdown; the site must never
leak raw syntax, internal ops data, or unbounded UI. Every assertion here is
a bug class that has actually bitten (or nearly)."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta

from engine import site

NASTY = """<!-- meta: items=47 groups=5 top="none" -->
# AI Signal — 2026-07-15

*47 items · 5 sources · 5 story groups (17 dropped as noise) · run 09:09 IST*

**Open pitches:** none proposed.

## Story threads

_Open events, last 10 days._

- `evt-20260714-nvidia` — internal registry-patch note, flagged for Saturday.

## Top stories

_Quiet day — nothing cleared the bar. 18 of 23 sources returned zero._

### Gemma 4 quantization lands

Intel published an official 2-bit quantization of Gemma 4.

↩ UPDATE to `evt-20260714-nvidia`

<details><summary>Go deeper</summary>

- `hn-algolia` · [Gemma 4 AutoRound thread](https://news.ycombinator.com/item?id=44) — 2026-07-15 06:20

</details>

## By beat

### inference-serving

- **llama.cpp, 8 releases today (`b10003`–`b10015`)** — [server gains
flags](https://github.com/g/l/releases/tag/b10010) and [a
refactor](https://github.com/g/l/releases/tag/b10011) are the only
user-facing changes.
- 3 posts were tagged by keyword coincidence — none relevant; dropped.
- [BIS bulletin PDF](https://www.bis.org/publ/bisbull120.pdf) — [hn-algolia]
- Inkling notes — [simon-willison](https://simonwillison.net/2026/Jul/16/inkling) ↩ `evt-20260716-inkling`

## Weird future section

Some unknown prose the parser has never seen.

## Mesh health

- `semianalysis` — stale feed.

*judged pass: sonnet, 2026-07-15 09:30 IST*
"""


class RenderRobustness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.mkdtemp()
        p = os.path.join(tmp, "2026-07-15.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write(NASTY)
        cls.day = site.parse_digest(p)
        cls.private = site.render_day_page(cls.day, None, None, {}, "now",
                                           public=False)
        cls.public = site.render_day_page(cls.day, None, None, {}, "now",
                                          public=True, excluded=[])
        cls.index = site.render_index_page([cls.day], "now", public=True)

    def test_no_raw_markdown_leaks(self):
        for html in (self.private, self.public, self.index):
            self.assertNotIn("**", html, "raw bold syntax leaked")
            self.assertNotIn("](http", html, "raw md link leaked")

    def test_none_headline_becomes_quiet_label(self):
        self.assertIn("Quiet day", self.index)
        self.assertNotIn(">none<", self.index.lower())

    def test_internal_ops_never_public(self):
        self.assertNotIn("evt-", self.public)
        self.assertNotIn("Story threads", self.public)
        self.assertNotIn("Mesh health", self.public)
        # private edition keeps them
        self.assertIn("Story threads", self.private)
        self.assertIn("Mesh health", self.private)

    def test_curation_notes_private_only(self):
        # editor-to-desk notes never reach readers; private edition keeps them
        self.assertNotIn('class="beat-note"', self.public)
        self.assertNotIn("keyword coincidence", self.public)
        self.assertIn('class="beat-note"', self.private)
        self.assertIn("keyword coincidence", self.private)

    def test_reader_labels_and_stats(self):
        self.assertIn("Also today", self.public)
        self.assertIn("By beat", self.private)
        self.assertNotIn("dropped as noise", self.public)

    def test_beat_labels_are_friendly(self):
        self.assertIn("Inference &amp; serving", self.public)
        self.assertNotIn("inference-serving", self.public)

    def test_quiet_note_survives_parsing(self):
        self.assertIn("nothing cleared the bar", self.public)

    def test_unknown_sections_render_not_crash(self):
        self.assertIn("Weird future section", self.private)

    def test_hardwrapped_bullets_reflow(self):
        # judges wrap long bullets across lines, splitting [text](url) —
        # the parser must rejoin them so links render whole
        self.assertNotIn("](", self.public)
        self.assertIn('href="https://github.com/g/l/releases/tag/b10010"',
                      self.public)
        self.assertIn("user-facing changes", self.public)

    def test_trailing_bare_sid_becomes_chip(self):
        # private edition: the raw "[sid]" tail renders as a chip
        self.assertIn('class="sid"', self.private)
        self.assertNotIn("[hn-algolia]", self.private)

    def test_collector_ids_never_public(self):
        # readers see link domains, not internal collector ids
        self.assertNotIn("hn-algolia", self.public)
        self.assertIn("hn-algolia", self.private)
        self.assertIn(">news.ycombinator.com<", self.public)

    def test_story_update_tag_stripped_from_public_gist(self):
        # FLAGS 2026-07-19: "↩ UPDATE to `evt-…`" paragraphs inside a TOP-STORY
        # gist leaked to the public page twice when left in by hand. The public
        # renderer must strip them; the private edition keeps them.
        self.assertNotIn("UPDATE to", self.public)
        self.assertNotIn("↩", self.public)
        self.assertIn("UPDATE to", self.private)
        # the story's real gist text must survive the strip
        self.assertIn("2-bit quantization", self.public)

    def test_story_tagged_beat_bullet_sanitized(self):
        # collector-appended " ↩ `evt-…`" on a by-beat bullet must not break
        # the bullet parse: public gets a clean domain chip, no evt-/sid leak;
        # private keeps the thread tag as a muted chip
        self.assertNotIn("simon-willison", self.public)
        self.assertIn(">simonwillison.net<", self.public)
        self.assertIn("↩ evt-20260716-inkling", self.private)

    def test_telemetry_never_public(self):
        # "47 items · 5 sources · run 09:09 IST" is desk accounting
        for needle in ("47 items", "5 sources", "run 09:09", "story groups"):
            self.assertNotIn(needle, self.public)
            self.assertNotIn(needle, self.index)
        self.assertIn("47 items", self.private)

    def test_meta_description_matches_config(self):
        class Cfg:
            public_footer_name = ""
            public_footer_url = ""
            public_footer_tagline = "a brief on everything"
            public_footer_links = []
            workspace_repo_url = ""

        pub = site.render_index_page([self.day], "now", public=True, cfg=Cfg())
        self.assertIn('content="AI Signal — a brief on everything"', pub)
        self.assertNotIn("AI infrastructure", pub)

    def test_beat_rows_not_flex(self):
        # flexbox on rich inline content shreds sentences into side-by-side
        # columns (live incident 2026-07-15) — beat rows must flow as text
        import re as _re
        rule = _re.search(r"\.beat-item\{[^}]*\}", self.public).group(0)
        self.assertNotIn("flex", rule)

    def test_footer_contact_links_config_driven(self):
        # readers must be able to reach the curator; links come ONLY from
        # config (the engine ships no personal data)
        class Cfg:
            public_footer_name = "Jane Doe"
            public_footer_url = "https://jane.example"
            public_footer_tagline = "a test brief"
            public_footer_links = [
                {"label": "GitHub", "url": "https://github.com/jane"},
                {"label": "LinkedIn", "url": "https://linkedin.com/in/jane"},
            ]

        pub = site.render_day_page(self.day, None, None, {}, "now",
                                   public=True, excluded=[], cfg=Cfg())
        self.assertIn('href="https://github.com/jane"', pub)
        self.assertIn('href="https://linkedin.com/in/jane"', pub)
        self.assertIn('curated by <a href="https://jane.example">Jane Doe</a>',
                      pub)
        # no cfg → generic footer, zero personal residue
        self.assertNotIn('class="foot-links"', self.public)
        self.assertNotIn("curated by", self.public)

    def test_browse_groups_weeks_under_collapsed_months(self):
        """Every week stays reachable, but only one month is expanded.

        The old flat chip bar truncated to six week chips, so most of the
        archive was simply unreachable from the index. Weeks now nest inside
        their month, so the invariant is no longer "few weeks" but "all weeks,
        one open month" — the control's resting size stays constant as the
        archive grows.
        """
        base = datetime(2026, 1, 5)
        days = []
        for w in range(30):
            d = dict(self.day)
            d["date_obj"] = base + timedelta(weeks=w)
            d["date"] = d["date_obj"].strftime("%Y-%m-%d")
            days.append(d)
        html = site.render_index_page(days, "now", public=True)

        # no week is dropped
        self.assertEqual(html.count('data-type="week"'), 30)
        # one month group per distinct month, only the newest expanded
        months = {d["date_obj"].strftime("%Y-%m") for d in days}
        self.assertEqual(html.count('<details class="br-mon"'), len(months))
        self.assertEqual(html.count('<details class="br-mon" open>'), 1)
        # and the old always-visible chip bar is gone
        self.assertNotIn('class="chips"', html)


if __name__ == "__main__":
    unittest.main()
