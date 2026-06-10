"""Tests for feed parsing — the part most likely to break as feeds change."""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from content_fetcher import _parse_date, _parse_feed_xml, _strip_html

OLD_CUTOFF = datetime(2000, 1, 1, tzinfo=timezone.utc)

RSS_SAMPLE = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Model X released</title>
    <link>https://example.com/model-x</link>
    <description>&lt;p&gt;Big &amp; bold launch.&lt;/p&gt;</description>
    <pubDate>Tue, 09 Jun 2026 12:00:00 GMT</pubDate>
  </item>
</channel></rss>"""

ATOM_SAMPLE = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>urn:1</id>
    <title>Atom post</title>
    <link rel="alternate" href="https://example.com/atom-post"/>
    <summary>An atom summary</summary>
    <published>2026-06-09T08:00:00Z</published>
    <author><name>Jane</name></author>
  </entry>
</feed>"""


def test_parse_rss():
    items = _parse_feed_xml(RSS_SAMPLE, "Test Feed", OLD_CUTOFF)
    assert len(items) == 1
    it = items[0]
    assert it.title == "Model X released"
    assert it.url == "https://example.com/model-x"
    assert "Big & bold launch." in it.summary  # HTML stripped, entities decoded
    assert it.source == "Test Feed"
    assert it.published_at.tzinfo is not None


def test_parse_atom():
    items = _parse_feed_xml(ATOM_SAMPLE, "Atom Feed", OLD_CUTOFF)
    assert len(items) == 1
    it = items[0]
    assert it.title == "Atom post"
    assert it.url == "https://example.com/atom-post"
    assert it.author == "Jane"


def test_cutoff_filters_old_items():
    future_cutoff = datetime.now(timezone.utc) + timedelta(days=1)
    assert _parse_feed_xml(RSS_SAMPLE, "Test", future_cutoff) == []


def test_malformed_xml_returns_empty():
    assert _parse_feed_xml(b"not xml at all <<<", "Bad", OLD_CUTOFF) == []


def test_parse_date_formats():
    rfc822 = _parse_date("Tue, 09 Jun 2026 12:00:00 GMT")
    iso = _parse_date("2026-06-09T12:00:00Z")
    assert rfc822 == iso
    assert _parse_date("") is None
    assert _parse_date("garbage") is None
    # naive datetimes get UTC attached
    naive = _parse_date("2026-06-09T12:00:00")
    assert naive is not None and naive.tzinfo is not None


def test_strip_html():
    assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"
    assert _strip_html("") == ""
    assert _strip_html(None) == ""
