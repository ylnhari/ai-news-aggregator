"""OpenRouter model-catalogue transport — new models and price changes.

`https://openrouter.ai/api/v1/models` is public JSON listing every routed model
with per-token prices. Link-diffing (htmldiff) cannot see a price change or a
new row, so this handler snapshots {model id: (name, prompt, completion)} and
emits an item per NEW model and per price move of >= PRICE_MOVE_PCT on either
side. First run only establishes the baseline.

Prices here are an aggregator's route, NOT the vendor's own price: treat every
item as a detection signal and re-verify on the vendor's page before quoting
(signaldesk hard rule 3).
"""

import hashlib
import json

from . import http

PRICE_MOVE_PCT = 10.0
MAX_ITEMS = 25  # a catalogue re-import must not flood the digest


def _per_million(raw):
    try:
        return round(float(raw) * 1_000_000, 4)
    except (TypeError, ValueError):
        return None


def _current(data):
    out = {}
    for m in (data.get("data") or []) if isinstance(data, dict) else []:
        mid = m.get("id")
        if not mid:
            continue
        pr = m.get("pricing") or {}
        out[mid] = {
            "id": mid,
            "name": m.get("name") or mid,
            "prompt": _per_million(pr.get("prompt")),
            "completion": _per_million(pr.get("completion")),
        }
    return out


def _moved(old, new):
    if old is None or new is None:
        return old != new
    if old == 0:
        return new != 0
    return abs(new - old) / old * 100.0 >= PRICE_MOVE_PCT


def _fmt(p):
    return "n/a" if p is None else f"${p:g}"


def diff(prev_by_id, current):
    """Pure function: returns (new_models, price_changes)."""
    new = [m for mid, m in current.items() if mid not in prev_by_id]
    changed = []
    for mid, m in current.items():
        o = prev_by_id.get(mid)
        if o and (_moved(o.get("prompt"), m["prompt"])
                  or _moved(o.get("completion"), m["completion"])):
            changed.append((o, m))
    return new, changed


def fetch(source, since, cfg):
    store = cfg._runtime_store
    current = _current(http.get_json(source.url))
    if not current:
        raise RuntimeError("openrouter: empty model list")

    prev = store.last_snapshot(source.id)
    items = []
    if prev:
        prev_by_id = {e["id"]: e for e in prev.get("extracted", [])}
        new, changed = diff(prev_by_id, current)
        for m in new[:MAX_ITEMS]:
            items.append({
                "url": f"https://openrouter.ai/{m['id']}",
                "title": f"New on OpenRouter: {m['name']}",
                "published_utc": "",
                "excerpt": (f"{m['name']} listed on OpenRouter at "
                            f"{_fmt(m['prompt'])} in / {_fmt(m['completion'])} out "
                            "per M tokens (aggregator route; verify on the "
                            "vendor's page)."),
                "beats": list(source.beats),
                "extra": {"discovered_via": "openrouter-snapshot",
                          "model_id": m["id"]},
            })
        for o, m in changed[:MAX_ITEMS]:
            items.append({
                "url": f"https://openrouter.ai/{m['id']}",
                "title": f"Price change on OpenRouter: {m['name']}",
                "published_utc": "",
                "excerpt": (f"{m['name']}: in {_fmt(o.get('prompt'))} -> "
                            f"{_fmt(m['prompt'])}, out {_fmt(o.get('completion'))} -> "
                            f"{_fmt(m['completion'])} per M tokens (aggregator "
                            "route; verify on the vendor's page)."),
                "beats": list(source.beats),
                "extra": {"discovered_via": "openrouter-snapshot",
                          "model_id": m["id"]},
            })

    extracted = list(current.values())
    h = hashlib.sha256(json.dumps(extracted, sort_keys=True).encode()).hexdigest()[:16]
    store.save_snapshot(source.id, source.url, h, extracted)
    return items
