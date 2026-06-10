"""Fetch real AI news from free, reliable sources: RSS/Atom feeds, arXiv, Hacker News.

All fetchers return NewsItem objects. Uses only stdlib XML parsing + requests,
so there are no fragile scraping dependencies.
"""

import json
import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)

USER_AGENT = "ai-news-aggregator/0.2 (personal daily digest)"

# XML namespaces used by Atom feeds (arXiv included)
ATOM_NS = "{http://www.w3.org/2005/Atom}"


@dataclass
class NewsItem:
    """A single news item from any source."""
    id: str
    title: str
    summary: str
    url: str
    source: str            # e.g. "OpenAI Blog", "arXiv cs.AI", "Hacker News"
    source_type: str       # "blog" | "research" | "community" | "social"
    published_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    author: str = ""
    score: int = 0         # engagement signal (HN points, etc.). 0 if unknown.
    verified: bool = True  # False for LLM-search-grounded items (may be inaccurate)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["published_at"] = self.published_at.isoformat()
        return d


def _parse_date(value: str) -> Optional[datetime]:
    """Parse RFC-822 (RSS) or ISO-8601 (Atom) dates into aware UTC datetimes."""
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _strip_html(text: str) -> str:
    """Cheap HTML tag removal for feed summaries."""
    import re
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _get(url: str, timeout: int = 20, **kwargs) -> Optional[requests.Response]:
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT}, **kwargs)
        resp.raise_for_status()
        return resp
    except Exception as e:
        logger.warning(f"Fetch failed for {url}: {e}")
        return None


# ---------------------------------------------------------------------------
# RSS / Atom blogs
# ---------------------------------------------------------------------------

def _parse_feed_xml(content: bytes, source_name: str, cutoff: datetime) -> List[NewsItem]:
    """Parse RSS 2.0 or Atom XML into NewsItems newer than cutoff."""
    items: List[NewsItem] = []
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        logger.warning(f"XML parse error for {source_name}: {e}")
        return items

    # RSS 2.0: <rss><channel><item>...
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = _strip_html(item.findtext("description") or "")[:500]
        author = (item.findtext("author") or item.findtext(
            "{http://purl.org/dc/elements/1.1/}creator") or "").strip()
        pub = _parse_date(item.findtext("pubDate") or "")
        if pub and pub < cutoff:
            continue
        items.append(NewsItem(
            id=link or title,
            title=title,
            summary=desc,
            url=link,
            source=source_name,
            source_type="blog",
            published_at=pub or datetime.now(timezone.utc),
            author=author,
        ))

    # Atom: <feed><entry>...
    for entry in root.iter(f"{ATOM_NS}entry"):
        title = (entry.findtext(f"{ATOM_NS}title") or "").strip()
        link = ""
        for link_el in entry.findall(f"{ATOM_NS}link"):
            if link_el.get("rel") in (None, "alternate"):
                link = link_el.get("href", "")
                break
        summary = _strip_html(
            entry.findtext(f"{ATOM_NS}summary") or entry.findtext(f"{ATOM_NS}content") or "")[:500]
        author = (entry.findtext(f"{ATOM_NS}author/{ATOM_NS}name") or "").strip()
        pub = _parse_date(
            entry.findtext(f"{ATOM_NS}published") or entry.findtext(f"{ATOM_NS}updated") or "")
        if pub and pub < cutoff:
            continue
        items.append(NewsItem(
            id=entry.findtext(f"{ATOM_NS}id") or link or title,
            title=title,
            summary=summary,
            url=link,
            source=source_name,
            source_type="blog",
            published_at=pub or datetime.now(timezone.utc),
            author=author,
        ))

    return items


def fetch_rss_feeds(feeds: List[dict], days_back: int = 1) -> List[NewsItem]:
    """Fetch items from a list of RSS/Atom feeds.

    Args:
        feeds: list of {"name": ..., "url": ...} dicts
        days_back: only keep items newer than this many days
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    all_items: List[NewsItem] = []

    for feed in feeds:
        name, url = feed.get("name", url_name(feed)), feed.get("url", "")
        if not url:
            continue
        resp = _get(url)
        if resp is None:
            continue
        items = _parse_feed_xml(resp.content, name, cutoff)
        logger.info(f"  RSS [{name}]: {len(items)} item(s) in window")
        all_items.extend(items)
        time.sleep(0.5)  # be polite

    return all_items


def url_name(feed: dict) -> str:
    from urllib.parse import urlparse
    return urlparse(feed.get("url", "")).netloc or "unknown feed"


# ---------------------------------------------------------------------------
# arXiv
# ---------------------------------------------------------------------------

def fetch_arxiv(categories: List[str], days_back: int = 1, max_results: int = 40) -> List[NewsItem]:
    """Fetch recent papers from arXiv API (free, no key needed)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    cat_query = "+OR+".join(f"cat:{c}" for c in categories)
    url = (
        "https://export.arxiv.org/api/query?"
        f"search_query={cat_query}&sortBy=submittedDate&sortOrder=descending"
        f"&max_results={max_results}"
    )
    resp = _get(url, timeout=30)
    if resp is None:
        return []

    items = _parse_feed_xml(resp.content, "arXiv", cutoff)
    for it in items:
        it.source_type = "research"
        it.source = "arXiv (" + ", ".join(categories) + ")"
    logger.info(f"  arXiv: {len(items)} paper(s) in window")
    return items


# ---------------------------------------------------------------------------
# Hacker News (Algolia API — free, no key)
# ---------------------------------------------------------------------------

def fetch_hackernews(queries: List[str], days_back: int = 1,
                     min_points: int = 30, per_query: int = 15) -> List[NewsItem]:
    """Fetch AI-related front-page-worthy stories from HN via Algolia search."""
    cutoff_ts = int((datetime.now(timezone.utc) - timedelta(days=days_back)).timestamp())
    seen: set = set()
    items: List[NewsItem] = []

    for query in queries:
        url = (
            "https://hn.algolia.com/api/v1/search?"
            f"query={requests.utils.quote(query)}&tags=story"
            f"&numericFilters=created_at_i>{cutoff_ts},points>{min_points}"
            f"&hitsPerPage={per_query}"
        )
        resp = _get(url)
        if resp is None:
            continue
        try:
            hits = resp.json().get("hits", [])
        except json.JSONDecodeError:
            continue

        for hit in hits:
            sid = hit.get("objectID")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            created = _parse_date(hit.get("created_at", "")) or datetime.now(timezone.utc)
            items.append(NewsItem(
                id=sid,
                title=hit.get("title", ""),
                summary=(hit.get("story_text") or "")[:300],
                url=hit.get("url") or f"https://news.ycombinator.com/item?id={sid}",
                source="Hacker News",
                source_type="community",
                published_at=created,
                author=hit.get("author", ""),
                score=hit.get("points", 0) or 0,
            ))
        time.sleep(0.3)

    items.sort(key=lambda x: x.score, reverse=True)
    logger.info(f"  Hacker News: {len(items)} story(ies) in window")
    return items


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

def fetch_all_sources(config) -> List[NewsItem]:
    """Fetch from every enabled source defined in config/settings.yaml."""
    days_back = config.days_back
    items: List[NewsItem] = []

    if config.get_setting("sources", "rss", "enabled", default=True):
        feeds = config.get_setting("sources", "rss", "feeds", default=[])
        logger.info(f"Fetching {len(feeds)} RSS/Atom feeds...")
        items.extend(fetch_rss_feeds(feeds, days_back))

    if config.get_setting("sources", "arxiv", "enabled", default=True):
        cats = config.get_setting("sources", "arxiv", "categories",
                                  default=["cs.AI", "cs.CL", "cs.LG"])
        max_papers = config.get_setting("sources", "arxiv", "max_results", default=40)
        logger.info(f"Fetching arXiv papers ({', '.join(cats)})...")
        # arXiv submissions are announced in batches; widen window slightly
        items.extend(fetch_arxiv(cats, max(days_back, 2), max_papers))

    if config.get_setting("sources", "hackernews", "enabled", default=True):
        queries = config.get_setting("sources", "hackernews", "queries",
                                     default=["AI", "LLM", "GPT", "open source model"])
        min_points = config.get_setting("sources", "hackernews", "min_points", default=30)
        logger.info("Fetching Hacker News stories...")
        items.extend(fetch_hackernews(queries, days_back, min_points))

    # Deduplicate by URL
    seen_urls = set()
    deduped = []
    for it in items:
        key = it.url or it.id
        if key in seen_urls:
            continue
        seen_urls.add(key)
        deduped.append(it)

    deduped.sort(key=lambda x: x.published_at, reverse=True)
    logger.info(f"Total items from real sources: {len(deduped)}")
    return deduped
