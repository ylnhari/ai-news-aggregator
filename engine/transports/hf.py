"""Hugging Face new-models transport — the models API, firehose-collapsed.

HF publishes hundreds of models a day; unfiltered this drowns the digest. We
keep only models whose id / tags / pipeline_tag match a keyword list in config
(`hf_keywords`) — the notable-family signal. createdAt is used for the window.

Quality gate (FLAGS.md 2026-07-18): the keyword match alone still let through
~95% personal-fine-tune noise ("Qwen2-0.5B-GRPO-test" matches "qwen" just
like a real Qwen release). A model must ALSO be either from a known-lab
namespace (`hf_known_labs`, matched against the "org/" prefix of the model
id) or have enough downloads/likes to show real traction (`hf_min_downloads`/
`hf_min_likes`) — whichever bar the model doesn't clear via its org, the
other must clear on its own. A fresh upload from a known lab still passes
even at zero downloads.
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


def _org(model_id: str) -> str:
    return model_id.split("/", 1)[0].lower() if "/" in model_id else ""


def _passes_quality_gate(model, model_id, cfg):
    known_labs = getattr(cfg, "hf_known_labs", None) or []
    if known_labs and _org(model_id) in known_labs:
        return True
    min_downloads = getattr(cfg, "hf_min_downloads", 0) or 0
    min_likes = getattr(cfg, "hf_min_likes", 0) or 0
    if not min_downloads and not min_likes:
        return True  # gate not configured — behave as before (keyword-only)
    downloads = model.get("downloads", 0) or 0
    likes = model.get("likes", 0) or 0
    return downloads >= min_downloads or likes >= min_likes


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
        if not _passes_quality_gate(m, model_id, cfg):
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
