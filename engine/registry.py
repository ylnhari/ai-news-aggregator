"""Read the machine registry (sources.json) from the private config repo.

The engine NEVER parses the human-authored sources.yaml (non-standard compact
syntax). It reads only the strict-JSON twin. See that file's `_comment`.
"""

import json


class Source:
    """One registry source, with the fields the transports care about."""

    def __init__(self, raw: dict):
        self.raw = raw
        self.id = raw.get("id", "")
        self.transport = raw.get("transport", "")
        self.handler = raw.get("handler", "unsupported")
        self.enabled = bool(raw.get("enabled", False))
        self.tier = raw.get("tier")
        self.trust = float(raw.get("trust", 0.5)) if raw.get("trust") is not None else 0.5
        self.beats = list(raw.get("beats", []))
        self.url = raw.get("url", "")
        self.skip_reason = raw.get("skip_reason", "")

    def get(self, key, default=None):
        return self.raw.get(key, default)

    def __repr__(self):
        return f"<Source {self.id} handler={self.handler} enabled={self.enabled}>"


def load_registry(path: str):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [Source(s) for s in data.get("sources", [])]


def enabled_sources(sources):
    return [s for s in sources if s.enabled and s.handler != "unsupported"]
