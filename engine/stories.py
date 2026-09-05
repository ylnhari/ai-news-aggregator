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

# Generic/cross-topic vocabulary that recurs across UNRELATED models' own
# coverage and must never, by itself, link a new item to an event that was
# actually about a different model -- a shared tier suffix ("flash", "sol":
# Gemini Flash, GLM Flash, Qwen Flash, GPT Sol are different families) or
# shared distribution/technique jargon ("locally runnable via Unsloth"
# appearing verbatim in both a Qwen HN post and an unrelated GLM-5.3 event).
# FLAGS.md 2026-09-02/2026-09-04: a Qwen item was auto-linked to the GLM-5.3
# event on (at least) three separate days purely over that shared phrase.
# Recommendation filed ledger/2026-W33.md §system-health.
#
# Scoped to CROSS-DAY matching only (not rank.py's same-day grouping): an
# open story's fingerprint keeps growing every time it's touched, so a short
# new headline's few remaining tokens can end up almost ENTIRELY contained
# in that big accumulated set (exactly what CONTAINMENT_LINK exists to
# catch) even when the new item is about a different model entirely.
# Same-day grouping compares two short headlines directly, where this
# failure mode doesn't arise the same way -- filtering there too was tried
# and cost a legitimate same-day merge (two GLM-5.3 items whose only
# non-anchor shared words happened to be exactly these), so that's left
# alone; rank.py's _ANCHOR_STOP instead gained "flash"/"sol" so they can
# never form a false anchor either.
_CROSS_TOPIC_STOP = {"flash", "sol", "unsloth", "locally", "runnable"}
# Bare version/size fragments ("27b", "70b", "1t", "8b") are meaningless
# without the model name attached; anchors are unaffected since _ANCHOR_RE
# binds a number to its preceding name before this filter ever runs.
_VERSION_FRAGMENT_RE = re.compile(r"^\d+(?:\.\d+)?[a-z]?$")


def _salient(tokens):
    """Strip generic cross-topic words and bare version/size fragments from
    a token set before it's used for cross-day story matching -- these must
    not by themselves establish a match; genuine identity still comes
    through _anchors() or whatever real content words remain."""
    return {t for t in tokens
            if t not in _CROSS_TOPIC_STOP and not _VERSION_FRAGMENT_RE.match(t)}


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
        st["_fp"] = _salient({t for t in fp if not t.startswith("#")})
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
        g_tokens_salient = _salient(g_tokens)

        best, best_j = None, -1.0
        for st in open_before:
            jac, cont = _overlap(g_tokens_salient, st["_fp"])
            # A shared named-release anchor (gemma4, gpt5…) IS the story link,
            # whatever the surrounding words; else fall back to token overlap.
            # KNOWN LIMITATION (FLAGS 07-19/21/22/23, distill 2026-08-01): a
            # single shared anchor also links unrelated items that merely name
            # the same model. Title tokens cannot separate those from real
            # follow-ups (verified against the Gemma-4-regression case); the
            # docstring's entity-extraction upgrade path is the real fix, the
            # judgment pass the mitigation meanwhile.
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
