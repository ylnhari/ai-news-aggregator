"""Daily ntfy.sh push — code, not a hand-typed command re-derived each run.

`python -m engine notify` sends one notification for a given day's digest:
the digest's own `top=` meta-line headline as the body, with a `Click`
header pointing at the public day page so tapping the notification opens
it (instead of just opening the ntfy app / copying text).

Both the ntfy topic and the site base URL are optional inputs, each with
an explicit non-fatal skip path — this must never raise on a missing
config, a missing digest, or a network failure. Every failure path is
reported back as a plain string; the ntfy topic itself is never included
in that string, even inside a caught exception's message (some HTTP
client errors embed the full request URL, which contains the topic).
"""

import os

import requests

from .digest import META_RE

NTFY_ENV_VAR = "NTFY_TOPIC"
_MAX_ATTEMPTS = 2  # one retry, per the "push failure is non-fatal" rule


class NotifyResult:
    def __init__(self, sent: bool, detail: str):
        self.sent = sent
        self.detail = detail


def _read_top_headline(digest_path: str):
    """Pull the `top="..."` field off the digest's first-line meta comment."""
    if not os.path.isfile(digest_path):
        return None
    with open(digest_path, "r", encoding="utf-8") as f:
        first_line = f.readline()
    m = META_RE.search(first_line)
    return m.group(3) if m else None


def day_page_url(cfg, date_str: str) -> str:
    """Public day-page URL for the Click header, or '' if unconfigured."""
    base = (getattr(cfg, "public_site_days_base_url", "") or "").strip()
    if not base:
        return ""
    return f"{base.rstrip('/')}/{date_str}.html"


def _redact(text: str, secret: str) -> str:
    if not secret:
        return text
    return text.replace(secret, "<redacted>")


def send_daily_push(cfg, digest_path: str, date_str: str, timeout: float = 10.0) -> NotifyResult:
    """Push the day's top headline to NTFY_TOPIC. Never raises.

    Returns a NotifyResult describing what happened — missing topic,
    missing digest, or a network failure are all reported the same way:
    sent=False with a human-readable, secret-free reason.
    """
    topic = os.environ.get(NTFY_ENV_VAR)
    if not topic:
        return NotifyResult(False, f"{NTFY_ENV_VAR} not set — skipped")

    headline = _read_top_headline(digest_path)
    if not headline:
        return NotifyResult(False, f"no digest / no meta line at {digest_path} — skipped")

    url = day_page_url(cfg, date_str)
    headers = {"Click": url} if url else {}

    last_err = None
    for _attempt in range(_MAX_ATTEMPTS):
        try:
            resp = requests.post(
                f"https://ntfy.sh/{topic}",
                data=headline.encode("utf-8"),
                headers=headers,
                timeout=timeout,
            )
        except requests.RequestException as e:
            last_err = _redact(str(e), topic)
            continue
        if resp.status_code == 200:
            note = "" if url else " (no Click link — public_site_days_base_url not configured)"
            return NotifyResult(True, f"sent{note}")
        last_err = f"HTTP {resp.status_code}"
    return NotifyResult(False, f"push failed after retry: {last_err}")
