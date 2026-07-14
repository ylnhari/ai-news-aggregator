"""Transport modules. Each exposes `fetch(source, since, cfg) -> list[item dict]`.

Normalized item dict shape:
    {
      "url":           canonical url (string, required — item id = sha1(url)),
      "title":         string,
      "published_utc": ISO-8601 string or "" (unknown),
      "excerpt":       string (cleaned, ~first 2k chars),
      "beats":         list[str]  (source-default beats; beats.py adds keyword hits),
      "extra":         dict       (transport-specific: hn points, hf tags, repo, ...),
    }

`source_id` is stamped on by the collector, not the transport.
Transports never raise a hard error to the caller: the collector wraps each in
try/except and records failures per-source. But defensive returns ([]) are still
preferred. To signal an EXPECTED, non-error skip (e.g. credentials not yet
configured), a transport raises `SkipSource(reason)` — the collector records it
as status "skipped" (mesh-health visible, watermark unchanged), never an error.
"""


class SkipSource(Exception):
    """Raised by a transport to signal a clean, expected skip (not a failure).

    Use for pending credentials or a deliberately-inactive configuration. The
    collector records status="skipped" with this message; the watermark is left
    untouched so the source self-heals its whole gap once it's configured."""


from . import rss, hn, hf, arxiv, greenhouse, htmldiff, reddit  # noqa: F401,E402

HANDLERS = {
    "rss": rss.fetch,
    "hn": hn.fetch,
    "hf": hf.fetch,
    "arxiv": arxiv.fetch,
    "greenhouse": greenhouse.fetch,
    "htmldiff": htmldiff.fetch,
    "reddit": reddit.fetch,
}

# Handlers gated on credentials from .env — doctor uses these to report a
# per-source pending-creds state without attempting a live fetch.
CREDS_CHECK = {
    "reddit": reddit.creds_ready,
}
