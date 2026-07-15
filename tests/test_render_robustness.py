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

*47 items · 5 sources · 5 story groups · run 09:09 IST*

**Open pitches:** none proposed.

## Story threads

_Open events, last 10 days._

- `evt-20260714-nvidia` — internal registry-patch note, flagged for Saturday.

## Top stories

_Quiet day — nothing cleared the bar. 18 of 23 sources returned zero._

## By beat

### inference-serving

- **llama.cpp, 8 releases today (`b10003`–`b10015`)** — [server gains flags](https://github.com/g/l/releases/tag/b10010) and [a refactor](https://github.com/g/l/releases/tag/b10011).
- 3 posts were tagged by keyword coincidence — none relevant; dropped.
- [BIS bulletin PDF](https://www.bis.org/publ/bisbull120.pdf) — [hn-algolia](https://news.ycombinator.com/item?id=1)

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

    def test_curation_notes_render_muted(self):
        self.assertIn("beat-note", self.public)
        self.assertIn("keyword coincidence", self.public)

    def test_beat_labels_are_friendly(self):
        self.assertIn("Inference &amp; serving", self.public)
        self.assertNotIn("inference-serving", self.public)

    def test_quiet_note_survives_parsing(self):
        self.assertIn("nothing cleared the bar", self.public)

    def test_unknown_sections_render_not_crash(self):
        self.assertIn("Weird future section", self.private)

    def test_week_chips_bounded(self):
        base = datetime(2026, 1, 5)
        days = []
        for w in range(30):
            d = dict(self.day)
            d["date_obj"] = base + timedelta(weeks=w)
            d["date"] = d["date_obj"].strftime("%Y-%m-%d")
            days.append(d)
        html = site.render_index_page(days, "now", public=True)
        self.assertLessEqual(html.count('data-type="week"'), 6)


if __name__ == "__main__":
    unittest.main()
