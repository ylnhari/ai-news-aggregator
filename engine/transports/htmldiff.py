"""HTML-diff transport — a watcher for sources with no feed.

Fetch the page, drop <script>/<style>, extract (link_text, href) pairs with
html.parser, resolve hrefs against the page, keep on-site content links, and
diff against the previous snapshot. Links not seen before become items; the new
snapshot is stored. First run = baseline (emits nothing) to avoid a cold flood.

'since' is not applicable here (pages carry no reliable per-link dates); novelty
is "not in the last snapshot", which is exactly what the watermark model wants.
"""

from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from . import http
from ..util import strip_html
from ..store import now_utc, iso


class _LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pairs = []           # (text, href)
        self._skip_depth = 0
        self._href = None
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip_depth += 1
            return
        if tag == "a":
            d = dict(attrs)
            self._href = d.get("href")
            self._buf = []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg"):
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if tag == "a" and self._href is not None:
            text = strip_html(" ".join(self._buf))
            self.pairs.append((text, self._href))
            self._href = None
            self._buf = []

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._href is not None:
            self._buf.append(data)


def _extract(html_text: str, page_url: str):
    parser = _LinkExtractor()
    try:
        parser.feed(html_text)
    except Exception:  # noqa: BLE001 — malformed HTML shouldn't kill the run
        pass
    host = urlparse(page_url).netloc.lower()
    seen = set()
    out = []
    for text, href in parser.pairs:
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        absu = urljoin(page_url, href)
        p = urlparse(absu)
        if p.scheme not in ("http", "https"):
            continue
        # keep on-site links with a non-trivial path and a real anchor text
        if p.netloc.lower() != host:
            continue
        if len(p.path.strip("/")) < 4:
            continue
        text = text.strip()
        if len(text) < 8:
            continue
        key = absu
        if key in seen:
            continue
        seen.add(key)
        out.append({"title": text[:300], "url": absu})
    return out


def fetch(source, since, cfg):
    store = cfg._runtime_store  # injected by collector
    html_text = http.get_text(source.url, accept="text/html")
    extracted = _extract(html_text, source.url)
    current_urls = {e["url"] for e in extracted}

    prev = store.last_snapshot(source.id)
    first_run = prev is None
    prev_urls = {e["url"] for e in prev.get("extracted", [])} if prev else set()

    items = []
    if not first_run:
        for e in extracted:
            if e["url"] in prev_urls:
                continue
            items.append({
                "url": e["url"],
                "title": e["title"],
                "published_utc": "",  # page links carry no reliable date
                "excerpt": f"New link on {source.id}: {e['title']}",
                "beats": list(source.beats),
                "extra": {"discovered_via": "html-diff"},
            })

    normalized_hash = str(hash(frozenset(current_urls)))
    store.save_snapshot(source.id, source.url, normalized_hash, extracted)
    return items
