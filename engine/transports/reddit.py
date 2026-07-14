"""Reddit transport via the official OAuth Data API — stdlib only.

Reddit killed the unauthenticated `.json` endpoint (403, TLS-fingerprint gated),
so this revives r/<sub> polling through a "script"-type OAuth app:

  1. POST https://www.reddit.com/api/v1/access_token
       HTTP Basic auth = client_id:client_secret
       body: grant_type=password&username=..&password=..   (script app, user ctx)
             or grant_type=client_credentials               (app-only, read-only)
  2. GET https://oauth.reddit.com/r/<sub>/top?t=day&limit=..
       Authorization: Bearer <token>   +   a descriptive User-Agent

Credentials come from the aggregator's gitignored .env (never signaldesk):
  REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET  (required)
  REDDIT_USERNAME / REDDIT_PASSWORD        (optional — enables the password grant)

Blank id/secret => SkipSource("credentials pending ...") — a clean, mesh-visible
skip, never a crash. Reddit requires a unique, descriptive UA; a browser UA gets
rate-limited, so we send our own.
"""

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import SkipSource
from ..util import getenv, canonical_url

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_BASE = "https://oauth.reddit.com"
TIMEOUT = 30


def _creds() -> dict:
    return {
        "client_id": getenv("REDDIT_CLIENT_ID", "").strip(),
        "client_secret": getenv("REDDIT_CLIENT_SECRET", "").strip(),
        "username": getenv("REDDIT_USERNAME", "").strip(),
        "password": getenv("REDDIT_PASSWORD", "").strip(),
    }


def creds_ready() -> bool:
    """True once the minimum (client id + secret) is configured. Used by doctor
    to report pending-creds without a live fetch."""
    c = _creds()
    return bool(c["client_id"] and c["client_secret"])


def _user_agent(username: str) -> str:
    who = f"by /u/{username}" if username else "signaldesk mesh"
    return f"signaldesk/0.1 (ai-news-aggregator; {who})"


def _get_token(creds: dict) -> str:
    auth = base64.b64encode(
        f"{creds['client_id']}:{creds['client_secret']}".encode("utf-8")
    ).decode("ascii")
    if creds["username"] and creds["password"]:
        form = {
            "grant_type": "password",
            "username": creds["username"],
            "password": creds["password"],
        }
    else:
        # app-only, read-only context — sufficient for public /top listings
        form = {"grant_type": "client_credentials"}
    body = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        headers={
            "Authorization": f"Basic {auth}",
            "User-Agent": _user_agent(creds["username"]),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    token = payload.get("access_token")
    if not token:
        raise RuntimeError(f"no access_token in response: {payload.get('error', payload)}")
    return token


def _get_listing(sub: str, token: str, ua: str, t: str, limit: int) -> dict:
    url = f"{API_BASE}/r/{sub}/top?t={urllib.parse.quote(t)}&limit={int(limit)}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "User-Agent": ua},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _subs_from_source(source) -> list:
    subs = source.get("subreddits")
    if subs:
        return list(subs)
    # derive from a /r/<sub> url if present, else default to the registry id target
    url = source.url or ""
    if "/r/" in url:
        return [url.split("/r/", 1)[1].split("/", 1)[0]]
    return ["LocalLLaMA"]


def parse_listing(data: dict, source, since, threshold: int) -> list:
    """Pure parser: Reddit Listing JSON -> normalized item dicts.

    Kept import-free of the network layer so it can be unit-tested against a
    constructed sample. Filters: drop stickied posts, posts older than `since`,
    and posts under the score `threshold`.
    """
    items = []
    children = (data or {}).get("data", {}).get("children", []) or []
    for child in children:
        d = child.get("data", {}) or {}
        if d.get("stickied"):
            continue
        score = int(d.get("score", 0) or 0)
        if score < threshold:
            continue
        created = d.get("created_utc")
        published = ""
        if created is not None:
            dt = datetime.fromtimestamp(float(created), tz=timezone.utc)
            if since and dt < since:
                continue
            published = dt.isoformat()
        permalink = d.get("permalink") or ""
        post_url = f"https://www.reddit.com{permalink}" if permalink else (d.get("url") or "")
        if not post_url:
            continue
        link_url = d.get("url") or ""
        selftext = (d.get("selftext") or "").strip()
        excerpt = selftext[:2000] if selftext else (
            f"link post → {link_url}" if link_url and link_url != post_url else ""
        )
        items.append({
            "url": canonical_url(post_url),
            "title": (d.get("title") or "").strip(),
            "published_utc": published,
            "excerpt": excerpt,
            "beats": list(source.beats),
            "extra": {
                "reddit_score": score,
                "reddit_comments": int(d.get("num_comments", 0) or 0),
                "subreddit": d.get("subreddit", ""),
                "author": d.get("author", ""),
                "link_url": link_url,
                "permalink": permalink,
            },
        })
    return items


def fetch(source, since, cfg):
    creds = _creds()
    if not creds["client_id"] or not creds["client_secret"]:
        raise SkipSource(
            "credentials pending — set REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET in "
            "ai-news-aggregator/.env (OPERATIONS.md §auth)"
        )

    token = _get_token(creds)
    ua = _user_agent(creds["username"])
    subs = _subs_from_source(source)
    t = source.get("t", "day")
    threshold = int(source.get("threshold", 0))
    limit = int(source.get("limit", 50))

    items = []
    seen = set()
    errors = []
    for sub in subs:
        try:
            data = _get_listing(sub, token, ua, t, limit)
        except Exception as e:  # noqa: BLE001 — per-subreddit soft failure
            errors.append(f"{sub}: {e}")
            continue
        for it in parse_listing(data, source, since, threshold):
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            items.append(it)
    if errors and not items:
        raise RuntimeError("; ".join(errors[:3]))
    return items
