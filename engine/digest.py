"""Digest writer — Markdown per run, in the OPERATIONS.md spirit.

Writes signaldesk/digests/YYYY-MM-DD.md (IST date) and regenerates INDEX.md.
No run with items => no file (caller prints a message instead). The digest is a
5-minute read: ranked headline groups with a <details> "go deeper" block, then
the rest bucketed by beat, plus a mesh-health footer. All heuristic — the LLM
judgment pass is explicitly flagged as pending.
"""

import glob
import os
import re
from datetime import datetime, timedelta, timezone

from .config import IST
from . import rank
from . import stories as stories_mod
from .registry import load_registry, enabled_sources
from .store import parse_iso

TOP_N = 7
MAX_DEEPER_LINKS = 12     # cap the per-story "Go deeper" list (viral clusters)
MAX_BEAT_GROUPS = 40      # cap the By-beat tail (backlog dumps after outages)
PENDING_MARKER = "LLM judgment pass pending"
META_RE = re.compile(r"<!--\s*meta:\s*items=(\d+)\s+groups=(\d+)\s+top=\"(.*?)\"\s*-->")
_DIGEST_FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")


def _ist_now():
    return datetime.now(IST)


def _fmt_ts(iso_str: str) -> str:
    dt = parse_iso(iso_str)
    if not dt:
        return "date?"
    return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M IST")


def _open_pitches(pitches_dir: str):
    out = []
    if not os.path.isdir(pitches_dir):
        return out
    for path in sorted(glob.glob(os.path.join(pitches_dir, "*.md"))):
        name = os.path.basename(path)
        if name.lower() == "readme.md":
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        if re.search(r"(?mi)^\s*status:\s*proposed\s*$", text):
            out.append(os.path.splitext(name)[0])
    return out


def _primary_beat(item, cfg):
    beats = item.get("beats", [])
    if not beats:
        return "uncategorized"
    return max(beats, key=lambda b: cfg.beat_weights.get(b, cfg.default_beat_weight))


def _gist(item) -> str:
    text = (item.get("excerpt") or "").strip()
    if not text:
        return "(no summary captured — open the source.)"
    text = " ".join(text.split())
    if len(text) > 420:
        text = text[:417].rsplit(" ", 1)[0] + "…"
    return text


def _group_source_ids(group):
    return sorted({it["source_id"] for it in group["items"]})


def build_markdown(cfg, groups, items, mesh, ist_dt, open_pitches,
                   open_stories=None):
    date_str = ist_dt.strftime("%Y-%m-%d")
    source_count = len({it["source_id"] for it in items})
    top = groups[:TOP_N]
    rest = groups[TOP_N:]

    L = []
    top_headline = top[0]["headline"] if top else "(no ranked story)"
    L.append(f'<!-- meta: items={len(items)} groups={len(groups)} '
             f'top="{top_headline.replace(chr(34), chr(39))[:120]}" -->')
    L.append(f"# AI Signal — {date_str}")
    L.append("")
    L.append(f"*{len(items)} items · {source_count} sources · "
             f"{len(groups)} story groups · run {ist_dt.strftime('%H:%M IST')}*")
    L.append("")

    if open_pitches:
        links = ", ".join(f"[{p}](../pitches/{p}.md)" for p in open_pitches)
        L.append(f"**Open pitches:** {links}")
    else:
        L.append("**Open pitches:** none proposed.")
    L.append("")

    # --- Story threads (the cross-day event ledger, delta-only recap) ---
    if open_stories:
        touched = {g["story"]["id"] for g in groups
                   if g.get("story") and g["story"]["status"] == "update"}
        L.append("## Story threads")
        L.append("")
        L.append("_Open events, last 10 days — one line each; full history "
                 "stays in the store, never re-read. • = updated this run._")
        L.append("")
        for st in open_stories[:15]:
            mark = "•" if st["id"] in touched else "·"
            seen = (st["last_seen_utc"] or "")[:10]
            L.append(f"- {mark} `{st['id']}` — {st['state']} "
                     f"(last seen {seen}, {st['item_count']} items)")
        L.append("")

    # --- Top stories ---
    L.append("## Top stories")
    L.append("")
    if not top:
        L.append("_No ranked stories this run._")
        L.append("")
    for g in top:
        primary = g["primary"]
        L.append(f"### {g['headline']}")
        L.append("")
        story = g.get("story")
        if story and story["status"] == "update":
            L.append(f"↩ UPDATE to `{story['id']}` (first seen {story['opened']}, "
                     f"{story['prior_items']} prior items) — prior state: "
                     f"{story['state']}")
            L.append("")
        L.append(_gist(primary))
        L.append("")
        L.append("<details><summary>Go deeper</summary>")
        L.append("")
        for it in g["items"][:MAX_DEEPER_LINKS]:
            ts = _fmt_ts(it.get("published_utc") or it.get("fetched_utc") or "")
            title = it.get("title") or it.get("url")
            L.append(f"- `{it['source_id']}` · [{title}]({it['url']}) — {ts}")
        overflow = len(g["items"]) - MAX_DEEPER_LINKS
        if overflow > 0:
            L.append(f"- …and {overflow} more corroborating link(s) (stored, "
                     f"query the engine by story id)")
        L.append("")
        L.append("</details>")
        L.append("")

    # --- By beat (the rest) ---
    if rest:
        L.append("## By beat")
        L.append("")
        shown = 0
        by_beat = {}
        for g in rest:
            beat = _primary_beat(g["primary"], cfg)
            by_beat.setdefault(beat, []).append(g)
        # order beats by weight desc
        for beat in sorted(by_beat, key=lambda b: cfg.beat_weights.get(b, cfg.default_beat_weight),
                           reverse=True):
            if shown >= MAX_BEAT_GROUPS:
                break
            L.append(f"### {beat}")
            L.append("")
            for g in by_beat[beat]:
                if shown >= MAX_BEAT_GROUPS:
                    break
                p = g["primary"]
                title = p.get("title") or p.get("url")
                tag = ""
                if g.get("story") and g["story"]["status"] == "update":
                    tag = f" ↩ `{g['story']['id']}`"
                L.append(f"- {title} — [{p['source_id']}]({p['url']}){tag}")
                shown += 1
            L.append("")
        hidden = len(rest) - shown
        if hidden > 0:
            L.append(f"_…{hidden} more low-ranked group(s) stored but not shown "
                     f"(backlog cap {MAX_BEAT_GROUPS}) — they remain queryable "
                     f"in the engine store._")
            L.append("")

    # --- Mesh health footer ---
    L.append("## Mesh health")
    L.append("")
    if mesh["errored"]:
        L.append("**Errored this run / last run:**")
        for sid, err in mesh["errored"]:
            L.append(f"- `{sid}` — {err}")
        L.append("")
    if mesh.get("pending"):
        L.append("**Credentials pending (skipped, mesh-visible):**")
        for sid, reason in mesh["pending"]:
            L.append(f"- `{sid}` — {reason}")
        L.append("")
    if mesh["zero"]:
        L.append("**Returned zero in window:** " + ", ".join(f"`{s}`" for s in mesh["zero"]))
        L.append("")
    if not mesh["errored"] and not mesh.get("pending") and not mesh["zero"]:
        L.append("All active sources returned items and none errored.")
        L.append("")
    L.append("_Draft quality: heuristic pre-rank — LLM judgment pass pending._")
    L.append("")
    return "\n".join(L)


def _mesh_health(cfg, store, items):
    """errored: sources whose last run errored. zero: enabled sources with no
    item in this window."""
    sources = load_registry(cfg.registry_path)
    active = enabled_sources(sources)
    active_ids = [s.id for s in active]
    contributing = {it["source_id"] for it in items}
    for it in items:
        # cross-source duplicates count as contribution — the source is alive
        contributing.update(a.get("source_id", "")
                            for a in (it.get("extra") or {}).get("also_seen", []))

    errored = []
    pending = []  # sources that returned a clean SkipSource (e.g. pending creds)
    for sid in active_ids:
        row = store.conn.execute(
            "SELECT last_status, last_error FROM source_runs WHERE source_id=?",
            (sid,),
        ).fetchone()
        if row and row["last_status"] == "error":
            errored.append((sid, (row["last_error"] or "")[:200]))
        elif row and row["last_status"] == "skipped":
            pending.append((sid, (row["last_error"] or "")[:200]))
    handled_ids = {e[0] for e in errored} | {p[0] for p in pending}
    zero = [sid for sid in active_ids if sid not in contributing and sid not in handled_ids]
    return {"errored": errored, "pending": pending, "zero": zero}


def _iso_week_monday(dt):
    return (dt - timedelta(days=dt.weekday())).date()


def regenerate_index(cfg):
    digests_dir = cfg.digests_dir
    entries = []  # (date_obj, filename, items, groups, top)
    for path in glob.glob(os.path.join(digests_dir, "*.md")):
        name = os.path.basename(path)
        if name.lower() in ("index.md", "readme.md"):
            continue
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})\.md$", name)
        if not m:
            continue
        d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        items = groups = 0
        top = "(no story)"
        try:
            with open(path, "r", encoding="utf-8") as f:
                head = f.read(4000)
            mm = META_RE.search(head)
            if mm:
                items, groups, top = int(mm.group(1)), int(mm.group(2)), mm.group(3)
            if top.strip().lower() in ("", "none", "(no ranked story)",
                                       "(no story)"):
                top = "Quiet day — scanned, nothing significant"
        except OSError:
            pass
        entries.append((d, name, items, groups, top))

    entries.sort(key=lambda e: e[0], reverse=True)

    L = ["# Digest index", "",
         "Newest first. Each line: date · top story · items/groups. "
         "Expand a digest for the full 5-minute read.", ""]
    cur_month = None
    cur_week = None
    for d, name, items, groups, top in entries:
        month_key = d.strftime("%Y-%m")
        if month_key != cur_month:
            cur_month = month_key
            cur_week = None
            L.append(f"## {month_key} ({d.strftime('%B')})")
            L.append("")
        wk = _iso_week_monday(d)
        if wk != cur_week:
            cur_week = wk
            L.append(f"### Week of {wk.strftime('%b %d')}")
            L.append("")
        L.append(f"- [{d.strftime('%Y-%m-%d')}]({name}) · {top} · {items} items / {groups} groups")
    if not entries:
        L.append("_No digests yet._")
    L.append("")

    os.makedirs(digests_dir, exist_ok=True)
    with open(os.path.join(digests_dir, "INDEX.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return len(entries)


def last_digest_before(cfg, date_str):
    """mtime of the most recently written digest file dated strictly before
    `date_str` (an IST "YYYY-MM-DD" digest filename), or None if none exists.

    Used to anchor `cmd_run`'s digest window on "since the last digest was
    written" rather than "since this invocation started" (FLAGS.md
    2026-08-27): a retry of `engine run` later the same day must not anchor
    on the in-progress digest THAT SAME RUN is about to rewrite -- doing so
    would silently drop whatever the first, partially-failed pass already
    collected, even though nothing was lost from the store. Excluding
    `date_str` itself means any number of same-day retries keep resolving to
    the same wide, correct window (the last completed prior-day digest);
    only the very first digest ever written has no such anchor and falls
    back to the caller's own run-start time.
    """
    latest_mtime = None
    for path in glob.glob(os.path.join(cfg.digests_dir, "*.md")):
        name = os.path.basename(path)
        m = _DIGEST_FILENAME_RE.match(name)
        if not m or m.group(1) >= date_str:
            continue
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if latest_mtime is None or mtime > latest_mtime:
            latest_mtime = mtime
    if latest_mtime is None:
        return None
    return datetime.fromtimestamp(latest_mtime, timezone.utc)


def build_digest(cfg, store, since):
    """Build (and write) the digest for items fetched since `since`.

    Returns the written file path, or None if there were zero items in window.
    """
    items = store.items_since(since)
    if not items:
        return None

    trust_by_source = {s.id: s.trust for s in load_registry(cfg.registry_path)}
    groups = rank.rank_and_group(items, trust_by_source, cfg)
    mesh = _mesh_health(cfg, store, items)
    open_pitches = _open_pitches(cfg.pitches_dir)

    ist_dt = _ist_now()
    date_str = ist_dt.strftime("%Y-%m-%d")

    os.makedirs(cfg.digests_dir, exist_ok=True)
    out_path = os.path.join(cfg.digests_dir, f"{date_str}.md")

    # Guard (ROADMAP known gap, now closed): NEVER clobber a same-day digest
    # that has already been through the LLM judgment pass — its footer no
    # longer carries the pending marker. New items stay in the store; the
    # next day's run picks the world up from there.
    if os.path.exists(out_path):
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                existing = f.read()
        except OSError:
            existing = ""
        if existing and PENDING_MARKER not in existing:
            print(f"  [guard] {os.path.basename(out_path)} is already judged — "
                  f"not overwriting. {len(items)} item(s) stored for the next run.")
            return None

    # Cross-day event ledger: link groups to open stories / open new ones.
    open_stories = stories_mod.assign_stories(store, groups, TOP_N, date_str)

    md = build_markdown(cfg, groups, items, mesh, ist_dt, open_pitches,
                        open_stories=open_stories)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)

    regenerate_index(cfg)

    # Regenerate the HTML edition ("AI Signal") so the primary read surface
    # stays current after every digest write. Lazy import avoids a cycle
    # (site imports constants from this module).
    try:
        from . import site
        site.build_site(cfg)
    except Exception as e:  # never let site rendering fail a digest write
        print(f"  [warn] site render failed: {e}")

    return out_path
