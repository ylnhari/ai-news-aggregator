"""Hacker News transport — Algolia search_by_date per query.

Each configured query is searched within the watermark window with a points
threshold; hits above threshold become items. The story's HN points are kept in
extra (rank.py normalizes them into the momentum term).
"""

from datetime import timezone
from urllib.parse import quote

from . import http
from ..util import to_iso


def fetch(source, since, cfg):
    queries = source.get("queries", [])
    threshold = int(source.get("threshold", 30))
    base = source.url or "https://hn.algolia.com/api/v1/search_by_date"
    since_ts = int(since.astimezone(timezone.utc).timestamp()) if since else 0

    items = []
    seen = set()
    errors = []
    for q in queries:
        numeric = f"created_at_i>{since_ts},points>{threshold}"
        url = (
            f"{base}?query={quote(q)}&tags=story"
            f"&numericFilters={quote(numeric)}&hitsPerPage=30"
        )
        try:
            data = http.get_json(url)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{q}: {e}")
            continue
        for hit in data.get("hits", []):
            oid = hit.get("objectID")
            if not oid or oid in seen:
                continue
            seen.add(oid)
            hn_url = f"https://news.ycombinator.com/item?id={oid}"
            target = hit.get("url") or hn_url
            points = hit.get("points", 0) or 0
            title = hit.get("title") or hit.get("story_title") or ""
            items.append({
                "url": target,
                "title": title,
                "published_utc": to_iso(hit.get("created_at", "")),
                "excerpt": (hit.get("story_text") or "")[:2000],
                "beats": list(source.beats),
                "extra": {
                    "hn_points": points,
                    "hn_comments": hit.get("num_comments", 0) or 0,
                    "hn_url": hn_url,
                    "hn_query": q,
                },
            })
    if errors and not items:
        raise RuntimeError("; ".join(errors[:3]))
    return items
