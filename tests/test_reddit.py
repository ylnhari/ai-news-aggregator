"""Unit tests for the Reddit transport — parser + graceful credential skip.

No network: `parse_listing` is a pure function exercised against a constructed
sample Listing, and the blank-credential path is tested via a monkeypatched
`_creds` so it never touches the wire.
"""

from datetime import datetime, timezone

import pytest

from engine.registry import Source
from engine.transports import SkipSource, reddit


def _source():
    return Source({
        "id": "reddit-localllama",
        "handler": "reddit",
        "enabled": True,
        "beats": ["open-weights", "community-pulse"],
        "url": "https://www.reddit.com/r/LocalLLaMA",
    })


def _sample_listing():
    # created_utc chosen well after the test's `since` cutoff below
    base = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc).timestamp()
    return {
        "kind": "Listing",
        "data": {
            "children": [
                {"kind": "t3", "data": {
                    "title": "Qwen3-Next-80B weights dropped, GGUFs already up",
                    "permalink": "/r/LocalLLaMA/comments/aaa/qwen3_next/",
                    "url": "https://huggingface.co/Qwen/Qwen3-Next-80B",
                    "created_utc": base,
                    "score": 412, "num_comments": 88,
                    "subreddit": "LocalLLaMA", "author": "someuser",
                    "selftext": "New MoE from Qwen, benchmarks inside.",
                    "stickied": False,
                }},
                {"kind": "t3", "data": {  # stickied -> dropped
                    "title": "Weekly Discussion Thread",
                    "permalink": "/r/LocalLLaMA/comments/bbb/weekly/",
                    "url": "https://www.reddit.com/r/LocalLLaMA/comments/bbb/weekly/",
                    "created_utc": base, "score": 200, "num_comments": 10,
                    "subreddit": "LocalLLaMA", "author": "mod", "stickied": True,
                }},
                {"kind": "t3", "data": {  # below threshold -> dropped
                    "title": "My tiny finetune experiment",
                    "permalink": "/r/LocalLLaMA/comments/ccc/tiny/",
                    "url": "https://www.reddit.com/r/LocalLLaMA/comments/ccc/tiny/",
                    "created_utc": base, "score": 5, "num_comments": 1,
                    "subreddit": "LocalLLaMA", "author": "someone",
                    "selftext": "small", "stickied": False,
                }},
                {"kind": "t3", "data": {  # older than `since` -> dropped
                    "title": "Old news from last month",
                    "permalink": "/r/LocalLLaMA/comments/ddd/old/",
                    "url": "https://www.reddit.com/r/LocalLLaMA/comments/ddd/old/",
                    "created_utc": datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp(),
                    "score": 999, "num_comments": 50,
                    "subreddit": "LocalLLaMA", "author": "x", "stickied": False,
                }},
            ]
        },
    }


def test_parse_listing_filters_and_maps():
    since = datetime(2026, 7, 13, tzinfo=timezone.utc)
    items = reddit.parse_listing(_sample_listing(), _source(), since, threshold=30)

    # only the one real, above-threshold, in-window, non-stickied post survives
    assert len(items) == 1
    it = items[0]
    assert it["url"] == "https://www.reddit.com/r/LocalLLaMA/comments/aaa/qwen3_next"
    assert it["title"].startswith("Qwen3-Next-80B")
    assert it["beats"] == ["open-weights", "community-pulse"]
    assert it["extra"]["reddit_score"] == 412
    assert it["extra"]["reddit_comments"] == 88
    assert it["extra"]["subreddit"] == "LocalLLaMA"
    assert it["extra"]["link_url"] == "https://huggingface.co/Qwen/Qwen3-Next-80B"
    assert it["published_utc"]  # non-empty ISO timestamp


def test_parse_listing_empty():
    assert reddit.parse_listing({}, _source(), None, threshold=0) == []
    assert reddit.parse_listing({"data": {"children": []}}, _source(), None, 0) == []


def test_fetch_skips_on_blank_credentials(monkeypatch):
    monkeypatch.setattr(reddit, "_creds", lambda: {
        "client_id": "", "client_secret": "", "username": "", "password": "",
    })
    with pytest.raises(SkipSource):
        reddit.fetch(_source(), None, cfg=None)


def test_creds_ready_reflects_blank(monkeypatch):
    monkeypatch.setattr(reddit, "_creds", lambda: {
        "client_id": "", "client_secret": "", "username": "", "password": "",
    })
    assert reddit.creds_ready() is False
    monkeypatch.setattr(reddit, "_creds", lambda: {
        "client_id": "id", "client_secret": "sec", "username": "", "password": "",
    })
    assert reddit.creds_ready() is True
