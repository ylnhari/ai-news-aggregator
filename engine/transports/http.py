"""Shared HTTP helpers — stdlib urllib only, real browser User-Agent, 30s timeout.

A transport failure must never crash a run. These helpers raise on hard failure;
the collector catches per-source. Callers that want soft failure use try/except.
"""

import gzip
import io
import urllib.request
import urllib.error

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
TIMEOUT = 30


def _open(url: str, accept: str = None):
    headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def get_bytes(url: str, accept: str = None, retries: int = 1) -> bytes:
    """One automatic retry on timeout — slow-but-healthy APIs (arXiv) time out
    transiently; a hard HTTP error still raises immediately."""
    attempt = 0
    while True:
        try:
            with _open(url, accept) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.GzipFile(fileobj=io.BytesIO(data)).read()
                return data
        except (TimeoutError, urllib.error.URLError) as e:
            is_timeout = isinstance(e, TimeoutError) or \
                isinstance(getattr(e, "reason", None), TimeoutError)
            if not is_timeout or attempt >= retries:
                raise
            attempt += 1


def get_text(url: str, accept: str = None, encoding: str = "utf-8") -> str:
    return get_bytes(url, accept).decode(encoding, errors="replace")


def get_json(url: str):
    import json
    return json.loads(get_bytes(url, accept="application/json"))
