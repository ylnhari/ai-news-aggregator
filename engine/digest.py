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
from .registry import load_registry, enabled_sources
from .store import parse_iso

TOP_N = 7
META_RE = re.compile(r"<!--\s*meta:\s*items=(\d+)\s+groups=(\d+)\s+top=\"(.*?)\"\s*-->")


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


def build_markdown(cfg, groups, items, mesh, ist_dt, open_pitches):
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
        L.append(_gist(primary))
        L.append("")
        L.append("<details><summary>Go deeper</summary>")
        L.append("")
        for it in g["items"]:
            ts = _fmt_ts(it.get("published_utc") or it.get("fetched_utc") or "")
            title = it.get("title") or it.get("url")
            L.append(f"- `{it['source_id']}` · [{title}]({it['url']}) — {ts}")
        L.append("")
        L.append("</details>")
        L.append("")

    # --- By beat (the rest) ---
    if rest:
        L.append("## By beat")
        L.append("")
        by_beat = {}
        for g in rest:
            beat = _primary_beat(g["primary"], cfg)
            by_beat.setdefault(beat, []).append(g)
        # order beats by weight desc
        for beat in sorted(by_beat, key=lambda b: cfg.beat_weights.get(b, cfg.default_beat_weight),
                           reverse=True):
            L.append(f"### {beat}")
            L.append("")
            for g in by_beat[beat]:
                p = g["primary"]
                title = p.get("title") or p.get("url")
                L.append(f"- {title} — [{p['source_id']}]({p['url']})")
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
    md = build_markdown(cfg, groups, items, mesh, ist_dt, open_pitches)

    os.makedirs(cfg.digests_dir, exist_ok=True)
    out_path = os.path.join(cfg.digests_dir, f"{ist_dt.strftime('%Y-%m-%d')}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)

    regenerate_index(cfg)
    return out_path
