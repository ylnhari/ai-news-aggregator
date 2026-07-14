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


def get_bytes(url: str, accept: str = None) -> bytes:
    with _open(url, accept) as resp:
        data = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            data = gzip.GzipFile(fileobj=io.BytesIO(data)).read()
        return data


def get_text(url: str, accept: str = None, encoding: str = "utf-8") -> str:
    return get_bytes(url, accept).decode(encoding, errors="replace")


def get_json(url: str):
    import json
    return json.loads(get_bytes(url, accept="application/json"))
