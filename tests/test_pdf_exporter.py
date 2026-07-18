"""Tests for markdown -> ReportLab conversion (escaping is security-relevant)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    from pdf_exporter import _md_to_rl
except ImportError as exc:  # reportlab is an optional dependency
    raise unittest.SkipTest(f"optional dependency missing: {exc}")


def test_escapes_xml_special_chars():
    # Unescaped & or < crashes ReportLab's doc.build()
    out = _md_to_rl("AT&T <script> rocks")
    assert "&amp;" in out
    assert "<script>" not in out


def test_bold_italic_code():
    assert "<b>big</b>" in _md_to_rl("**big** news")
    assert "<i>subtle</i>" in _md_to_rl("a *subtle* hint")
    assert 'face="Courier"' in _md_to_rl("run `pip install`")


def test_markdown_link():
    out = _md_to_rl("[OpenAI](https://openai.com/news)")
    assert '<link href="https://openai.com/news"' in out
    assert "<u>OpenAI</u>" in out


def test_bare_url_becomes_link():
    out = _md_to_rl("see https://example.com/x for details")
    assert '<link href="https://example.com/x"' in out


def test_no_double_linking():
    out = _md_to_rl("[label](https://example.com/page)")
    # the URL inside href must not itself be wrapped in another <link>
    assert out.count("<link href=") == 1
