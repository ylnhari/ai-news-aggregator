"""arXiv transport — export.arxiv.org Atom query within the watermark window.

The registry supplies a narrowed `query` (abs:-scoped). arXiv has no server-side
date filter on this endpoint, so we sort by submittedDate desc and stop keeping
once entries fall before `since`.
"""

import xml.etree.ElementTree as ET
from urllib.parse import quote

from . import http
from ..util import parse_date, strip_html, to_iso

ATOM = "{http://www.w3.org/2005/Atom}"


def fetch(source, since, cfg):
    base = source.url or "http://export.arxiv.org/api/query"
    query = source.get("query", "cat:cs.LG")
    url = (
        f"{base}?search_query={quote(query)}"
        f"&sortBy=submittedDate&sortOrder=descending&max_results=60"
    )
    content = http.get_bytes(url, accept="application/atom+xml")
    root = ET.fromstring(content)

    items = []
    for entry in root.iter(f"{ATOM}entry"):
        title = " ".join((entry.findtext(f"{ATOM}title") or "").split())
        link = ""
        for le in entry.findall(f"{ATOM}link"):
            if le.get("rel") in (None, "alternate"):
                link = le.get("href", "")
                break
        if not link:
            link = (entry.findtext(f"{ATOM}id") or "").strip()
        pub = parse_date(
            entry.findtext(f"{ATOM}published") or entry.findtext(f"{ATOM}updated") or ""
        )
        if pub and since and pub < since:
            # descending order → everything after this is older too
            break
        summary = strip_html(entry.findtext(f"{ATOM}summary") or "")[:2000]
        authors = [
            (a.findtext(f"{ATOM}name") or "").strip()
            for a in entry.findall(f"{ATOM}author")
        ]
        if not (link or title):
            continue
        items.append({
            "url": link,
            "title": title,
            "published_utc": to_iso(pub) if pub else "",
            "excerpt": summary,
            "beats": list(source.beats),
            "extra": {"authors": authors[:8]},
        })
    return items
