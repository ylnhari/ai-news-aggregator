"""Hugging Face new-models transport — the models API, firehose-collapsed.

HF publishes hundreds of models a day; unfiltered this drowns the digest. We
keep only models whose id / tags / pipeline_tag match a keyword list in config
(`hf_keywords`) — the notable-family signal. createdAt is used for the window.
"""

from . import http
from ..util import to_iso


def _matches(model, keywords):
    hay = " ".join([
        str(model.get("id", "")),
        str(model.get("pipeline_tag", "")),
        " ".join(model.get("tags", []) or []),
    ]).lower()
    return any(k in hay for k in keywords)


def fetch(source, since, cfg):
    url = source.url or (
        "https://huggingface.co/api/models?sort=createdAt&direction=-1&limit=50"
    )
    # request tags + createdAt explicitly so the payload carries what we filter on
    if "full=" not in url and "expand" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}full=true"
    data = http.get_json(url)
    keywords = cfg.hf_keywords or []

    items = []
    for m in data:
        created = to_iso(m.get("createdAt", ""))
        if since and created:
            from ..util import parse_date
            dt = parse_date(created)
            if dt and dt < since:
                continue
        if keywords and not _matches(m, keywords):
            continue
        model_id = m.get("id") or m.get("modelId") or ""
        if not model_id:
            continue
        items.append({
            "url": f"https://huggingface.co/{model_id}",
            "title": f"HF model: {model_id}",
            "published_utc": created,
            "excerpt": (
                f"pipeline={m.get('pipeline_tag', '?')} "
                f"downloads={m.get('downloads', 0)} likes={m.get('likes', 0)} "
                f"tags={', '.join((m.get('tags') or [])[:8])}"
            ),
            "beats": list(source.beats),
            "extra": {
                "hf_id": model_id,
                "pipeline_tag": m.get("pipeline_tag", ""),
                "tags": (m.get("tags") or [])[:20],
                "likes": m.get("likes", 0),
                "downloads": m.get("downloads", 0),
            },
        })
    return items
