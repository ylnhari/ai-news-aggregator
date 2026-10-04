"""Page-content diff transport — detects changed TEXT on a single page.

htmldiff only sees new links, so a price edit, a new row in a supported-models
table or a re-ordered leaderboard is invisible to it. This handler fetches one
page (HTML or raw markdown), reduces it to normalized visible-text lines,
diffs against the previous snapshot and emits ONE item per run when lines were
added or removed, quoting a few of the added lines. First run is the baseline.

Volatile lines (build ids, timestamps, cookie banners) would cause noise, so a
change must touch at least MIN_CHANGED_LINES lines, and lines are normalized
(digits-only/hash-like tokens kept, whitespace collapsed). Pricing pages are
the intended use: treat the item as a pointer to re-read the vendor page, not
as a verified fact.
"""

import hashlib
import re
from collections import Counter

from . import http
from ..util import strip_html

MIN_CHANGED_LINES = 2
MAX_SNAPSHOT_LINES = 4000
SAMPLE_LINES = 6

_SCRIPT_RE = re.compile(r"(?is)<(script|style|noscript|svg)\b.*?</\1>")
_BLOCK_RE = re.compile(r"(?i)</?(p|div|li|tr|td|th|h[1-6]|br|section|table|ul|ol)\b[^>]*>")


def _lines(text: str):
    if "<html" in text[:2000].lower() or "<body" in text[:5000].lower():
        text = _SCRIPT_RE.sub(" ", text)
        text = _BLOCK_RE.sub("\n", text)
        text = strip_html(text.replace("\n", "\x00")).replace("\x00", "\n") \
            if False else "\n".join(strip_html(x) for x in text.split("\n"))
    out = []
    for ln in text.splitlines():
        ln = re.sub(r"\s+", " ", ln).strip()
        if len(ln) < 2:
            continue
        out.append(ln)
        if len(out) >= MAX_SNAPSHOT_LINES:
            break
    return out


def diff_lines(old, new):
    """Pure: (added, removed) line lists, order-insensitive."""
    # multiset diff: a price cell repeated on a page ("$2") must still count
    co, cn = Counter(old), Counter(new)
    added, removed = [], []
    for l in new:
        if cn[l] > co[l]:
            added.append(l)
            cn[l] -= 1
    for l in old:
        if co[l] > cn.get(l, 0) and l not in set(new):
            removed.append(l)
    return added, removed


def fetch(source, since, cfg):
    store = cfg._runtime_store
    new = _lines(http.get_text(source.url))
    if len(new) < 5:
        raise RuntimeError(f"pagediff: only {len(new)} text lines extracted")

    prev = store.last_snapshot(source.id)
    items = []
    if prev:
        old = [e["l"] for e in prev.get("extracted", [])]
        added, removed = diff_lines(old, new)
        if len(added) + len(removed) >= MIN_CHANGED_LINES:
            sample = "; ".join(l[:140] for l in added[:SAMPLE_LINES]) or \
                "(lines removed only)"
            h = hashlib.sha256("\n".join(added + removed).encode()).hexdigest()[:10]
            items.append({
                "url": f"{source.url}#change-{h}",
                "title": f"Page changed: {source.id}",
                "published_utc": "",
                "excerpt": (f"{len(added)} line(s) added, {len(removed)} removed on "
                            f"{source.url}. Added: {sample}"),
                "beats": list(source.beats),
                "extra": {"discovered_via": "page-diff", "added": len(added),
                          "removed": len(removed)},
            })
    h = hashlib.sha256("\n".join(new).encode()).hexdigest()[:16]
    store.save_snapshot(source.id, source.url, h, [{"l": l} for l in new])
    return items
