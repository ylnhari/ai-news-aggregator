"""Small shared helpers: date parsing, html stripping, canonical URLs, .env."""

import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, urlunparse


# --- .env loader (stdlib; no python-dotenv dependency) ---------------------
# The engine reads auth-backed source credentials from a gitignored .env at the
# aggregator repo root (never from signaldesk). os.environ always wins over the
# file so CI/shell secrets override it. Missing keys read as "" — callers treat
# blank as "not configured" (a clean skip), never a crash.
_ENV_CACHE = None


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env(path: str = None) -> dict:
    """Parse KEY=VALUE lines from .env into a dict (cached for the default path).

    Ignores blank lines and #-comments; strips one layer of surrounding quotes.
    A missing file yields {} — the engine runs fine with no .env at all."""
    global _ENV_CACHE
    if path is None and _ENV_CACHE is not None:
        return _ENV_CACHE
    p = path or os.path.join(_repo_root(), ".env")
    data = {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                data[key] = val
    except OSError:
        pass
    if path is None:
        _ENV_CACHE = data
    return data


def getenv(key: str, default: str = "") -> str:
    """Config value: real environment first, then the .env file, then default."""
    if os.environ.get(key):
        return os.environ[key]
    return load_env().get(key, default)


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
