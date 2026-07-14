"""RSS 2.0 + Atom transport via xml.etree.

Handles GitHub `releases.atom` feeds: a source carrying a `repos:` list is
expanded into one feed URL per repo (repo_feed_template). Otherwise the single
`url` is fetched. Only items published at/after `since` are returned; items with
no parseable date are kept (feeds often omit dates and we prefer over-reporting
to silently dropping a real release).
"""

import xml.etree.ElementTree as ET

from . import http
from ..util import parse_date, strip_html, to_iso

ATOM = "{http://www.w3.org/2005/Atom}"
MEDIA = "{http://purl.org/dc/elements/1.1/}"


def _feed_urls(source):
    repos = source.get("repos")
    if repos:
        tmpl = source.get("repo_feed_template", "https://github.com/{repo}/releases.atom")
        return [(tmpl.format(repo=r), r) for r in repos]
    return [(source.url, None)]


def _parse(content: bytes, since, default_beats, repo_label):
    items = []
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return items

    # RSS 2.0
    for el in root.iter("item"):
        title = (el.findtext("title") or "").strip()
        link = (el.findtext("link") or "").strip()
        desc = strip_html(el.findtext("description") or "")[:2000]
        pub = parse_date(el.findtext("pubDate") or "")
        if pub and since and pub < since:
            continue
        if not (link or title):
            continue
        items.append(_mk(title, link, desc, pub, default_beats, repo_label))

    # Atom
    for entry in root.iter(f"{ATOM}entry"):
        title = (entry.findtext(f"{ATOM}title") or "").strip()
        link = ""
        for le in entry.findall(f"{ATOM}link"):
            if le.get("rel") in (None, "alternate"):
                link = le.get("href", "")
                break
        if not link:
            le = entry.find(f"{ATOM}link")
            link = le.get("href", "") if le is not None else ""
        summary = strip_html(
            entry.findtext(f"{ATOM}summary") or entry.findtext(f"{ATOM}content") or ""
        )[:2000]
        pub = parse_date(
            entry.findtext(f"{ATOM}published") or entry.findtext(f"{ATOM}updated") or ""
        )
        if pub and since and pub < since:
            continue
        if not (link or title):
            continue
        items.append(_mk(title, link, summary, pub, default_beats, repo_label))

    return items


def _mk(title, link, excerpt, pub, default_beats, repo_label):
    extra = {}
    if repo_label:
        extra["repo"] = repo_label
    return {
        "url": link,
        "title": title,
        "published_utc": to_iso(pub) if pub else "",
        "excerpt": excerpt,
        "beats": list(default_beats),
        "extra": extra,
    }


def fetch(source, since, cfg):
    items = []
    errors = []
    for url, repo in _feed_urls(source):
        try:
            content = http.get_bytes(url, accept="application/rss+xml, application/atom+xml, application/xml, text/xml")
            items.extend(_parse(content, since, source.beats, repo))
        except Exception as e:  # noqa: BLE001 — per-feed soft failure
            errors.append(f"{repo or url}: {e}")
    if errors and not items:
        # every sub-feed failed → surface as a source failure
        raise RuntimeError("; ".join(errors[:3]))
    return items
