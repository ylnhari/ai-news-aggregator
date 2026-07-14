"""Beat tagger — keyword map from config, additive to source-default beats.

An item's beats = its source's default beats (already on the dict) UNION any
beat whose keywords appear in the title/excerpt/extra text. No LLM: this is a
cheap first-pass ontology tag; the real classification is the later judgment pass.
"""


# Only genuinely content-bearing extra fields feed classification. Fields like
# hn_query / hn_url would otherwise leak the *search term* (e.g. "GPU") into every
# story returned for that query, mis-tagging unrelated items.
_CONTENT_EXTRA_KEYS = ("tags", "pipeline_tag", "repo", "hf_id", "authors", "location")


def _haystack(item: dict) -> str:
    parts = [item.get("title", ""), item.get("excerpt", "")]
    extra = item.get("extra", {})
    for key in _CONTENT_EXTRA_KEYS:
        v = extra.get(key)
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, list):
            parts.append(" ".join(str(x) for x in v))
    return " ".join(parts).lower()


def tag(item: dict, beat_keywords: dict) -> list:
    """Return the item's beat list (source defaults + keyword hits), de-duped,
    order-stable (source defaults first)."""
    beats = list(item.get("beats", []))
    hay = _haystack(item)
    for beat, keywords in (beat_keywords or {}).items():
        if beat in beats:
            continue
        for kw in keywords:
            if kw and kw.lower() in hay:
                beats.append(beat)
                break
    return beats


def tag_all(items, beat_keywords):
    for it in items:
        it["beats"] = tag(it, beat_keywords)
    return items
