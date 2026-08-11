"""engine.notify — no network, no NTFY_TOPIC required.

Covers the promise this module exists to keep: the daily push always
carries a tap-to-open Click link when the site base URL is configured,
and never has to be re-derived by hand each run (FLAGS.md 2026-08-11 —
a hand-typed push dropped the Click header because nothing enforced it).

Also covers the non-fatal skip paths (missing topic, missing digest) and
that a caught network error never leaks the ntfy topic into the reported
message.
"""

import os
import tempfile
import unittest
from unittest import mock

from engine import notify


class CfgStub:
    def __init__(self, base_url=""):
        self.public_site_days_base_url = base_url


def _write_digest(path, top):
    with open(path, "w", encoding="utf-8") as f:
        f.write(f'<!-- meta: items=3 groups=2 top="{top}" -->\n# AI Signal\n')


class ReadTopHeadlineTests(unittest.TestCase):
    def test_reads_top_field_from_meta_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "2026-08-11.md")
            _write_digest(p, "Meta ships Muse Glimmer")
            self.assertEqual(notify._read_top_headline(p), "Meta ships Muse Glimmer")

    def test_missing_file_returns_none(self):
        self.assertIsNone(notify._read_top_headline("/no/such/digest.md"))

    def test_no_meta_line_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "2026-08-11.md")
            with open(p, "w", encoding="utf-8") as f:
                f.write("# AI Signal\nno meta comment here\n")
            self.assertIsNone(notify._read_top_headline(p))


class DayPageUrlTests(unittest.TestCase):
    def test_builds_url_from_base(self):
        cfg = CfgStub("https://example.github.io/ai-signal/days")
        self.assertEqual(
            notify.day_page_url(cfg, "2026-08-11"),
            "https://example.github.io/ai-signal/days/2026-08-11.html",
        )

    def test_strips_trailing_slash(self):
        cfg = CfgStub("https://example.github.io/ai-signal/days/")
        self.assertEqual(
            notify.day_page_url(cfg, "2026-08-11"),
            "https://example.github.io/ai-signal/days/2026-08-11.html",
        )

    def test_empty_base_returns_empty(self):
        cfg = CfgStub("")
        self.assertEqual(notify.day_page_url(cfg, "2026-08-11"), "")


class SendDailyPushTests(unittest.TestCase):
    def test_skips_without_raising_when_topic_unset(self):
        cfg = CfgStub("https://example.github.io/ai-signal/days")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "2026-08-11.md")
            _write_digest(p, "headline")
            env = dict(os.environ)
            env.pop(notify.NTFY_ENV_VAR, None)
            with mock.patch.dict(os.environ, env, clear=True):
                result = notify.send_daily_push(cfg, p, "2026-08-11")
        self.assertFalse(result.sent)
        self.assertIn("not set", result.detail)

    def test_skips_without_raising_when_digest_missing(self):
        cfg = CfgStub("https://example.github.io/ai-signal/days")
        with mock.patch.dict(os.environ, {notify.NTFY_ENV_VAR: "super-secret-topic"}):
            result = notify.send_daily_push(cfg, "/no/such/digest.md", "2026-08-11")
        self.assertFalse(result.sent)
        self.assertNotIn("super-secret-topic", result.detail)

    def test_sends_with_click_header_when_base_url_configured(self):
        cfg = CfgStub("https://example.github.io/ai-signal/days")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "2026-08-11.md")
            _write_digest(p, "headline")
            fake_resp = mock.Mock(status_code=200)
            with mock.patch.dict(os.environ, {notify.NTFY_ENV_VAR: "topic-x"}), \
                 mock.patch("engine.notify.requests.post", return_value=fake_resp) as post:
                result = notify.send_daily_push(cfg, p, "2026-08-11")
        self.assertTrue(result.sent)
        _, kwargs = post.call_args
        self.assertEqual(
            kwargs["headers"]["Click"],
            "https://example.github.io/ai-signal/days/2026-08-11.html",
        )
        self.assertEqual(kwargs["data"], b"headline")

    def test_sends_without_click_header_when_base_url_unset(self):
        cfg = CfgStub("")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "2026-08-11.md")
            _write_digest(p, "headline")
            fake_resp = mock.Mock(status_code=200)
            with mock.patch.dict(os.environ, {notify.NTFY_ENV_VAR: "topic-x"}), \
                 mock.patch("engine.notify.requests.post", return_value=fake_resp) as post:
                result = notify.send_daily_push(cfg, p, "2026-08-11")
        self.assertTrue(result.sent)
        self.assertIn("no Click link", result.detail)
        _, kwargs = post.call_args
        self.assertEqual(kwargs["headers"], {})

    def test_network_error_message_never_contains_the_topic(self):
        cfg = CfgStub("")
        secret_topic = "extremely-secret-topic-value"
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "2026-08-11.md")
            _write_digest(p, "headline")
            import requests as requests_mod

            def _boom(*a, **kw):
                raise requests_mod.ConnectionError(
                    f"Failed to establish connection to https://ntfy.sh/{secret_topic}"
                )

            with mock.patch.dict(os.environ, {notify.NTFY_ENV_VAR: secret_topic}), \
                 mock.patch("engine.notify.requests.post", side_effect=_boom):
                result = notify.send_daily_push(cfg, p, "2026-08-11", timeout=0.01)
        self.assertFalse(result.sent)
        self.assertNotIn(secret_topic, result.detail)
        self.assertIn("<redacted>", result.detail)

    def test_retries_once_then_reports_failure(self):
        cfg = CfgStub("")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "2026-08-11.md")
            _write_digest(p, "headline")
            fake_resp = mock.Mock(status_code=500)
            with mock.patch.dict(os.environ, {notify.NTFY_ENV_VAR: "topic-x"}), \
                 mock.patch("engine.notify.requests.post", return_value=fake_resp) as post:
                result = notify.send_daily_push(cfg, p, "2026-08-11")
        self.assertFalse(result.sent)
        self.assertEqual(post.call_count, notify._MAX_ATTEMPTS)
        self.assertIn("HTTP 500", result.detail)


if __name__ == "__main__":
    unittest.main()
