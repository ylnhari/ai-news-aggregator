"""Story (event) tracking — the minimal cross-day event ledger.

Implements signaldesk SCHEMAS.md's EVENT object in its leanest useful form:
a `stories` SQLite table (see store.py) plus token-overlap matching that links
each digest run's story GROUPS to open stories from previous days. The digest
then reads "UPDATE to evt-…" instead of treating every day as day zero, and
the judgment agent gets one-line recaps of open threads — never full history
(the delta-only pattern; Anthropic context-engineering guidance).

Honest about being heuristic: matching is salient-token overlap, the same
machinery rank.py uses for same-day grouping. Entity extraction / embeddings
are the upgrade path if false merges ever show up in practice; the judgment
pass is the human-in-the-loop that catches them meanwhile (Techmeme model:
the algorithm proposes, the editor decides).
"""

import re

from .rank import _tokens, _anchors

_SLUG_RE = re.compile(r"[^a-z0-9]+")

# Auto-link when the new group's salient tokens overlap an open story's
# accumulated fingerprint strongly enough. Two thresholds, either suffices:
JACCARD_LINK = 0.35        # symmetric similarity
CONTAINMENT_LINK = 0.6     # most of the (short) new headline is known tokens
MATCH_WINDOW_DAYS = 10     # research consensus: story clusters cap ~10 days


def slugify(title: str, max_tokens: int = 4) -> str:
    toks = [t for t in _SLUG_RE.split((title or "").lower()) if t][:max_tokens]
    return "-".join(toks) or "untitled"


def _overlap(a: set, b: set):
    if not a or not b:
        return 0.0, 0.0
    inter = len(a & b)
    return inter / len(a | b), inter / min(len(a), len(b))


def assign_stories(store, groups, top_n, date_str):
    """Match every group against open stories; create stories for unmatched
    TOP-N groups. Mutates each group with g["story"]:
      {"id", "status": "update"|"new", "opened", "prior_items"} or None.
    Returns the list of open stories AFTER assignment (for the digest recap).
    """
    open_before = store.open_stories(days=MATCH_WINDOW_DAYS)
    for st in open_before:
        fp = set((st["fingerprint"] or "").split())
        st["_fp"] = {t for t in fp if not t.startswith("#")}
        st["_anchors"] = {t[1:] for t in fp if t.startswith("#")}

    for gi, g in enumerate(groups):
        g["story"] = None
        g_tokens, g_anchors = set(), set()
        for it in g["items"]:
            g_tokens |= it.get("_tokens") or _tokens(it.get("title", ""))
            g_anchors |= it.get("_anchors") if it.get("_anchors") is not None \
                else _anchors(it.get("title", ""))
        if not g_tokens:
            continue

        best, best_j = None, -1.0
        for st in open_before:
            jac, cont = _overlap(g_tokens, st["_fp"])
            # A shared named-release anchor (gemma4, gpt5…) IS the story link,
            # whatever the surrounding words; else fall back to token overlap.
            if (g_anchors & st["_anchors"]) or jac >= JACCARD_LINK \
                    or cont >= CONTAINMENT_LINK:
                score = jac + (1.0 if g_anchors & st["_anchors"] else 0.0)
                if score > best_j:
                    best, best_j = st, score
        fp_new = " ".join(g_tokens | {f"#{a}" for a in g_anchors})
        if best is not None:
            store.touch_story(best["id"], g["headline"],
                              fp_new, len(g["items"]))
            store.link_items_to_story([it["id"] for it in g["items"]], best["id"])
            g["story"] = {"id": best["id"], "status": "update",
                          "opened": (best["opened_utc"] or "")[:10],
                          "prior_items": best["item_count"] or 0,
                          "state": best["state"] or best["title"]}
        elif gi < top_n:
            sid = f"evt-{date_str.replace('-', '')}-{slugify(g['headline'])}"
            store.create_story(sid, g["headline"],
                               " ".join(sorted(g_tokens | {f'#{a}' for a in g_anchors})[:40]))
            store.touch_story(sid, g["headline"], "", len(g["items"]))
            store.link_items_to_story([it["id"] for it in g["items"]], sid)
            g["story"] = {"id": sid, "status": "new", "opened": date_str,
                          "prior_items": 0, "state": g["headline"]}
    store.commit()
    return store.open_stories(days=MATCH_WINDOW_DAYS)
