"""Small shared helpers: date parsing, html stripping, canonical URLs."""

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, urlunparse


def parse_date(value: str):
    """Parse RFC-822 (RSS) or ISO-8601 (Atom/API) into an aware UTC datetime."""
    if not value:
        return None
    value = value.strip()
    dt = None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_iso(value):
    """Normalize a date string or datetime into an ISO-8601 UTC string ("" if unknown)."""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = parse_date(value or "")
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(text: str) -> str:
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " "))
    return _WS_RE.sub(" ", text).strip()


def canonical_url(url: str) -> str:
    """Canonicalize for identity: drop fragment, strip common tracking params,
    lowercase scheme/host, drop trailing slash on the path."""
    if not url:
        return url
    try:
        p = urlparse(url.strip())
    except ValueError:
        return url.strip()
    scheme = (p.scheme or "https").lower()
    netloc = p.netloc.lower()
    path = p.path.rstrip("/") or "/"
    query = p.query
    if query:
        keep = []
        for part in query.split("&"):
            key = part.split("=", 1)[0].lower()
            if key.startswith("utm_") or key in ("fbclid", "gclid", "ref", "ref_src"):
                continue
            keep.append(part)
        query = "&".join(keep)
    return urlunparse((scheme, netloc, path, "", query, ""))
