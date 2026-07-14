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


def _root_domain(url: str) -> str:
    host = urlparse(url or "").netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


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
            elif items[i]["_root"] and items[i]["_root"] == items[j]["_root"] and jac > 0.3:
                union(i, j)

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
