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
Transports never raise to the caller: the collector wraps each in try/except
and records failures per-source. But defensive returns ([]) are still preferred.
"""

from . import rss, hn, hf, arxiv, greenhouse, htmldiff  # noqa: F401

HANDLERS = {
    "rss": rss.fetch,
    "hn": hn.fetch,
    "hf": hf.fetch,
    "arxiv": arxiv.fetch,
    "greenhouse": greenhouse.fetch,
    "htmldiff": htmldiff.fetch,
}
