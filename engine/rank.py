"""Heuristic PRE-ranking only — honest about being a cheap first pass.

The real editorial judgment (event clustering + significance scoring) is a later
LLM pass. Here we only:
  score = source trust weight x max beat weight  +  normalized HN points
and naively group near-duplicate stories (title-token Jaccard > 0.5, or a shared
root domain with moderate overlap) so obvious cross-source echoes collapse.
"""

import re
from urllib.parse import urlparse

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is",
    "are", "new", "how", "why", "what", "at", "by", "from", "this", "that", "as",
    "it", "its", "we", "you", "your", "vs", "via", "now", "our",
}


def _tokens(title: str):
    return {t for t in _TOKEN_RE.findall((title or "").lower())
            if t not in _STOP and len(t) > 2}


# "Gemma 4" / "GPT-5" / "Llama 3.1" → gemma4 / gpt5 / llama3.1 — the named
# release is the strongest signal that two headlines are the same story, even
# when every other word differs ("X released" vs "bug found in X").
_ANCHOR_RE = re.compile(r"\b([a-z][a-z]{2,})[\s\-]?v?(\d+(?:\.\d+)?)\b")
_ANCHOR_STOP = _STOP | {"top", "best", "part", "year", "week", "day", "step"}


def _anchors(title: str):
    out = set()
    for name, num in _ANCHOR_RE.findall((title or "").lower()):
        if name not in _ANCHOR_STOP:
            out.add(f"{name}{num}")
    return out


def _root_domain(url: str) -> str:
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    root = ".".join(parts[-2:]) if len(parts) >= 2 else host
    # Mono-host platforms: scope to the repo/org, not the whole domain —
    # and mark repo-scoped roots so same-repo bursts (8 point releases in a
    # day) collapse to ONE group regardless of title overlap.
    if root in ("github.com", "gitlab.com", "huggingface.co"):
        segs = [s for s in parsed.path.split("/") if s][:2]
        if segs:
            return root + "/" + "/".join(segs)
    return root


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def score_item(item: dict, source_trust: float, cfg) -> float:
    beats = item.get("beats", [])
    if beats:
        max_beat_w = max(
            cfg.beat_weights.get(b, cfg.default_beat_weight) for b in beats
        )
    else:
        max_beat_w = cfg.default_beat_weight
    base = float(source_trust) * float(max_beat_w)

    points = item.get("extra", {}).get("hn_points", 0) or 0
    momentum = min(points, cfg.hn_points_norm) / cfg.hn_points_norm if points else 0.0
    return round(base + momentum, 4)


def rank_and_group(items, trust_by_source: dict, cfg):
    """Score every item, then union-find group near-duplicates.

    Returns a list of groups (highest score first). Each group:
      {headline, score, primary (item), items[]}.
    """
    for it in items:
        it["_score"] = score_item(it, trust_by_source.get(it["source_id"], 0.5), cfg)
        it["_tokens"] = _tokens(it.get("title", ""))
        it["_anchors"] = _anchors(it.get("title", ""))
        it["_root"] = _root_domain(it.get("url", ""))

    n = len(items)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        for j in range(i + 1, n):
            jac = _jaccard(items[i]["_tokens"], items[j]["_tokens"])
            if jac > 0.5:
                union(i, j)
            elif items[i]["_root"] and items[i]["_root"] == items[j]["_root"] \
                    and ("/" in items[i]["_root"] or jac > 0.3):
                # same domain needs title overlap; same REPO groups outright
                union(i, j)
            elif (items[i]["_anchors"] & items[j]["_anchors"]) and jac > 0.15:
                union(i, j)  # same named release, cross-source echo

    clusters = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(items[i])

    groups = []
    for members in clusters.values():
        members.sort(key=lambda x: x["_score"], reverse=True)
        primary = members[0]
        groups.append({
            "headline": primary.get("title", "") or "(untitled)",
            "score": primary["_score"],
            "primary": primary,
            "items": members,
        })
    groups.sort(key=lambda g: g["score"], reverse=True)
    return groups
