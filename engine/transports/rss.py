"""RSS 2.0 + Atom transport via xml.etree.

Handles GitHub `releases.atom` feeds: a source carrying a `repos:` list is
expanded into one feed URL per repo (repo_feed_template). Otherwise the single
`url` is fetched. Only items published at/after `since` are returned; items with
no parseable date are kept (feeds often omit dates and we prefer over-reporting
to silently dropping a real release).

Repo-expanded feeds also get a build-tag noise filter (FLAGS.md 2026-07-18):
some repos (llama.cpp) tag every CI build with a bare id like "b10056" and
ship several a day. Each such tag shares no token/anchor with the next one,
so it was opening its own single-item story every day. A title matching
`release_tag_noise_regex` is dropped unless the title/body names a known
model or architecture — see `_filter_noise_build_tags`.
"""

import re
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


def _filter_noise_build_tags(items, cfg):
    """Drop bare per-build release tags (e.g. llama.cpp's "b10056") that name
    no known model/architecture — recurring CI noise that would otherwise
    open its own single-item story every day (FLAGS.md 2026-07-18).

    Only ever touches items from repo-expanded GH release feeds (those carry
    `extra["repo"]`, set by `_mk` when `source.repos` is configured); plain
    RSS/Atom items are untouched. Only titles that FULLY match the noise
    regex are candidates — real titles ("v1.2.3 — feature", "Release v5.14.0")
    never match. Even a matching tag is kept if its title or excerpt mentions
    a configured model/architecture keyword, so a build that actually adds a
    named model is never suppressed.
    """
    if not getattr(cfg, "release_tag_filter_enabled", True):
        return items
    pattern = getattr(cfg, "release_tag_noise_regex", "") or ""
    if not pattern:
        return items
    try:
        noise_re = re.compile(pattern)
    except re.error:
        return items
    keywords = getattr(cfg, "release_notable_model_keywords", None) or []

    kept = []
    for it in items:
        if it.get("extra", {}).get("repo") and noise_re.match((it.get("title") or "").strip()):
            hay = f"{it.get('title', '')} {it.get('excerpt', '')}".lower()
            if not any(k in hay for k in keywords):
                continue  # suppressed: bare build tag, no named model/arch
        kept.append(it)
    return kept


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
    return _filter_noise_build_tags(items, cfg)
