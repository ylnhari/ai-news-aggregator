"""Site builder — the HTML edition of the signaldesk digest ("AI Signal").

Parses every `digests/YYYY-MM-DD.md` (the exact Markdown that `engine/digest.py`
writes) plus any `pitches/*.md` carrying `status: proposed`, and renders two
self-contained artifacts into `signaldesk/site/`:

  - index.html    a full standalone page (doctype/html/head/body), openable
                  via file:// on a laptop.
  - artifact.html the SAME page as a body-content-only fragment (no wrapper
                  tags; inline <title> + one <style> + one <script>), for
                  publishing as a claude.ai artifact (which supplies its own
                  <!doctype>/<head>/<body> skeleton).

Both share ONE renderer; only the outer shell differs. Zero external requests:
no CDNs, no webfonts, no remote images — system font stacks and inline CSS/JS
only. The Markdown stays the record; this is the primary READ surface.

CLI: `python -m engine site`. Also invoked automatically after every digest
write (see engine/digest.py) so the page stays current.
"""

import glob
import html
import os
import re
from datetime import datetime

from .config import IST
# Mirror the writer's structure knowledge — share its constants rather than
# re-deriving them, so the parser tracks the writer if it changes.
from .digest import META_RE, TOP_N  # noqa: F401  (TOP_N documents the top/rest split)

# The private config repo on GitHub — pitch files are linked here (the digest's
# own "open pitches" line uses a repo-relative path; the site needs an absolute
# one for the artifact surface where relative links have no base).
SIGNALDESK_REPO = "https://github.com/ylnhari/signaldesk"
# Author attribution target for the public footer byline (attribution is welcome;
# tailoring the system around the editor is not — signaldesk CLAUDE.md rule 1).
PUBLIC_SITE_URL = "https://ylnhari.github.io"

# --- public-edition sanitizer -------------------------------------------------
# The public GitHub Pages edition drops the private radar: pitches, the
# target-employers beat, any careers-* source item, and mesh health. Filtering
# lives in the generator (a flag), never a hand-maintained copy of the template.
CAREERS_PREFIX = "careers-"
TARGET_BEATS = {"target-employers", "target employers"}


def _is_careers(sid):
    return (sid or "").lower().startswith(CAREERS_PREFIX)

_DATE_FILE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.md$")
_H1_DATE_RE = re.compile(r"#\s*AI Signal\s*[—-]\s*(\d{4}-\d{2}-\d{2})")
# Top-story "go deeper" bullet: - `sid` · [title](url) — timestamp
_SRC_BULLET_RE = re.compile(
    r"^-\s+`([^`]+)`\s*·\s*\[(.*)\]\(([^)]+)\)\s*[—-]\s*(.*)$")
# By-beat bullet: - <title> — [sid](url)   (title may itself contain " — ")
_BEAT_BULLET_RE = re.compile(r"^-\s+(.*)\s+[—-]\s+\[([^\]]+)\]\(([^)]+)\)\s*$")
# Any inline markdown link, for defensive pass-through / url extraction.
_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)]+)\)")


# --------------------------------------------------------------------------- #
# Parsing                                                                      #
# --------------------------------------------------------------------------- #

def _week_monday(d):
    """Monday-start week key (a date) for grouping/labels."""
    return d - _timedelta_days(d.weekday())


def _timedelta_days(n):
    from datetime import timedelta
    return timedelta(days=n)


def parse_digest(path):
    """Parse one digest .md into a structured day-section dict.

    Defensive: unrecognized `## ` sections pass through as muted paragraphs and
    a malformed file yields whatever parsed plus a `note`, never an exception.
    """
    name = os.path.basename(path)
    day = {
        "file": name, "date": None, "date_obj": None, "stats": "",
        "meta": None, "top": [], "beats": [], "mesh": [], "passthrough": [],
        "notes": [],
    }
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        day["notes"].append(f"unreadable: {e}")
        return day

    m = _DATE_FILE_RE.match(name)
    if m:
        day["date"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    mm = META_RE.search(text)
    if mm:
        day["meta"] = {"items": int(mm.group(1)), "groups": int(mm.group(2)),
                       "top": mm.group(3)}

    section = None          # None | 'top' | 'beat' | 'mesh' | 'passthrough'
    cur_story = None
    cur_beat = None
    stats_seen = False
    footer_seen = False

    lines = text.split("\n")
    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        if line.startswith("<!--"):
            continue
        h1 = _H1_DATE_RE.search(line)
        if h1:
            day["date"] = day["date"] or h1.group(1)
            continue

        # Italic lines: first one before Top stories is the stats line; a later
        # `*judged pass…*` / `*Draft quality…*` is the footer — stop mesh there.
        if stripped.startswith("*") and stripped.endswith("*") and len(stripped) > 2:
            inner = stripped.strip("*").strip()
            if not stats_seen and section is None:
                day["stats"] = inner
                stats_seen = True
                continue
            if section == "mesh" and ("judged pass" in inner.lower()
                                      or "draft quality" in inner.lower()):
                footer_seen = True
                section = None
                continue

        if stripped.startswith("**Open pitches:**"):
            # Proposed pitches drive the urgent card from the pitch files
            # themselves; this line is informational only.
            continue

        if line.startswith("## "):
            title = line[3:].strip().lower()
            cur_story = None
            cur_beat = None
            if title.startswith("top stories"):
                section = "top"
            elif title.startswith("by beat"):
                section = "beat"
            elif title.startswith("mesh health"):
                section = "mesh"
            else:
                section = "passthrough"
                day["passthrough"].append({"title": line[3:].strip(), "body": []})
            continue

        if line.startswith("### "):
            head = line[4:].strip()
            if section == "top":
                cur_story = {"headline": head, "gist": [], "sources": []}
                day["top"].append(cur_story)
            elif section == "beat":
                cur_beat = {"name": head, "items": []}
                day["beats"].append(cur_beat)
            elif section == "passthrough" and day["passthrough"]:
                day["passthrough"][-1]["body"].append(head)
            continue

        if section == "top" and cur_story is not None:
            if not stripped or stripped.startswith("<details") \
                    or stripped.startswith("<summary") \
                    or stripped.startswith("</details") \
                    or stripped.startswith("</summary"):
                continue
            sm = _SRC_BULLET_RE.match(stripped)
            if sm:
                cur_story["sources"].append({
                    "sid": sm.group(1), "title": sm.group(2),
                    "url": sm.group(3), "ts": sm.group(4).strip()})
                continue
            if stripped.startswith("- "):
                # An unexpected bullet shape — keep it as gist text, don't drop.
                cur_story["gist"].append(stripped[2:])
                continue
            cur_story["gist"].append(stripped)
            continue

        if section == "beat" and cur_beat is not None and stripped.startswith("- "):
            bm = _BEAT_BULLET_RE.match(stripped)
            if bm:
                cur_beat["items"].append({
                    "title": bm.group(1).strip(), "sid": bm.group(2),
                    "url": bm.group(3)})
            else:
                cur_beat["items"].append({
                    "title": stripped[2:].strip(), "sid": "", "url": ""})
            continue

        if section == "mesh" and not footer_seen and stripped:
            day["mesh"].append(stripped)
            continue

        if section == "passthrough" and stripped and day["passthrough"]:
            day["passthrough"][-1]["body"].append(stripped)
            continue

    if day["date"]:
        try:
            day["date_obj"] = datetime.strptime(day["date"], "%Y-%m-%d")
        except ValueError:
            day["notes"].append(f"bad date: {day['date']}")
    if day["date_obj"] is None:
        day["notes"].append("no parseable date — placed last")
    if not day["top"] and not day["beats"]:
        day["notes"].append("no stories parsed (malformed or empty digest)")
    return day


def parse_pitch(path):
    """Extract {date, status, title, whynow, file} from a pitch .md."""
    name = os.path.basename(path)
    out = {"file": name, "date": None, "status": "", "title": "", "whynow": ""}
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return out
    dm = re.search(r"(\d{4})(\d{2})(\d{2})", name)
    if dm:
        out["date"] = f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}"
    sm = re.search(r"(?mi)^\s*status:\s*(\S+)\s*$", text)
    if sm:
        out["status"] = sm.group(1).strip().lower()
    # Prefer the Angle's bold-quoted headline; fall back to a slug-derived title.
    am = re.search(r"##\s*Angle\s*\n+.*?\*\*[\"“]?(.+?)[\"”]?\*\*", text, re.S)
    if am:
        out["title"] = am.group(1).strip()
    else:
        slug = re.sub(r"^pitch-\d{8}-", "", os.path.splitext(name)[0])
        out["title"] = slug.replace("-", " ").strip() or name
    wm = re.search(r"##\s*Why now\s*\n+(.+?)(?:\n\s*\n|\n#)", text, re.S)
    if wm:
        out["whynow"] = " ".join(wm.group(1).split())
    return out


# --------------------------------------------------------------------------- #
# Rendering                                                                    #
# --------------------------------------------------------------------------- #

def _esc(s):
    return html.escape(s or "", quote=True)


def _attr(s):
    return html.escape(s or "", quote=True)


def _md_inline(s):
    """Minimal inline Markdown → HTML for mesh/pass-through text: links, code,
    bold. Everything else is escaped."""
    parts = []
    i = 0
    for mo in _LINK_RE.finditer(s):
        parts.append(_md_bold_code(s[i:mo.start()]))
        parts.append(f'<a href="{_attr(mo.group(2))}">{_esc(mo.group(1))}</a>')
        i = mo.end()
    parts.append(_md_bold_code(s[i:]))
    return "".join(parts)


def _md_bold_code(s):
    out = _esc(s)
    out = re.sub(r"`([^`]+)`", lambda m: f"<code>{m.group(1)}</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", lambda m: f"<strong>{m.group(1)}</strong>", out)
    return out


def _render_story(story):
    head = _esc(story["headline"])
    gist_html = ""
    # gist lines split into paragraphs on blank separators
    para, paras = [], []
    for ln in story["gist"]:
        if ln.strip():
            para.append(ln.strip())
        elif para:
            paras.append(" ".join(para))
            para = []
    if para:
        paras.append(" ".join(para))
    for p in paras:
        gist_html += f"<p>{_md_bold_code(p)}</p>"

    src_rows = ""
    for s in story["sources"]:
        src_rows += (
            '<li class="src">'
            f'<span class="sid">{_esc(s["sid"])}</span>'
            f'<a class="src-title" href="{_attr(s["url"])}">{_esc(s["title"])}</a>'
            f'<span class="ts">{_esc(s["ts"])}</span>'
            '</li>')
    details = ""
    if src_rows:
        details = (
            '<details class="deeper"><summary>Sources &amp; depth</summary>'
            f'<ul class="src-list">{src_rows}</ul></details>')
    return (f'<article class="story"><h3>{head}</h3>{gist_html}{details}</article>')


def _render_beats(beats, public=False, excluded=None):
    if not beats:
        return ""
    rows = ""
    for b in beats:
        if public and b["name"].strip().lower() in TARGET_BEATS:
            if excluded is not None:
                excluded.append(f"beat '{b['name']}' ({len(b['items'])} item(s))")
            continue
        items = ""
        for it in b["items"]:
            if public and _is_careers(it["sid"]):
                if excluded is not None:
                    excluded.append(f"careers item [{it['sid']}] {it['title'][:60]}")
                continue
            if it["url"]:
                title = f'<a href="{_attr(it["url"])}">{_esc(it["title"])}</a>'
            else:
                title = _esc(it["title"])
            chip = f'<span class="sid">{_esc(it["sid"])}</span>' if it["sid"] else ""
            items += f'<li class="beat-item">{title}{chip}</li>'
        if not items:
            continue
        rows += (
            '<div class="beat">'
            f'<div class="beat-name">{_esc(b["name"])}</div>'
            f'<ul class="beat-items">{items}</ul></div>')
    if not rows:
        return ""
    return (
        '<div class="eyebrow">By beat</div>'
        f'<div class="beats">{rows}</div>')


def _render_mesh(mesh):
    if not mesh:
        return ""
    body = ""
    for ln in mesh:
        if ln.startswith("- "):
            body += f'<li>{_md_inline(ln[2:])}</li>'
        else:
            body += f'<p>{_md_inline(ln)}</p>'
    # wrap consecutive <li> in a <ul>? Keep simple: a flow list is acceptable.
    body = re.sub(r"(?:<li>.*?</li>)+",
                  lambda m: f"<ul>{m.group(0)}</ul>", body)
    return ('<details class="mesh"><summary>Mesh health</summary>'
            f'<div class="mesh-body">{body}</div></details>')


def _render_urgent(pitch):
    link = f"{SIGNALDESK_REPO}/blob/main/pitches/{pitch['file']}"
    why = f'<p class="why">{_esc(pitch["whynow"])}</p>' if pitch["whynow"] else ""
    return (
        '<aside class="urgent">'
        '<div class="eyebrow urgent-eyebrow">Open pitch</div>'
        f'<h3 class="urgent-title">{_esc(pitch["title"])}</h3>'
        f'{why}'
        f'<a class="urgent-link" href="{_attr(link)}">View pitch file &rarr;</a>'
        '</aside>')


def _render_day(day, pitches_by_date, public=False, excluded=None):
    date = day["date"] or "unknown-date"
    date_obj = day["date_obj"]
    if date_obj:
        pretty = date_obj.strftime("%A, %b %-d, %Y") if os.name != "nt" \
            else date_obj.strftime("%A, %b %#d, %Y")
        month_key = date_obj.strftime("%Y-%m")
        week_key = _week_monday(date_obj).strftime("%Y-%m-%d")
    else:
        pretty, month_key, week_key = date, "unknown", "unknown"

    note_html = ""
    for n in day["notes"]:
        note_html += f"<!-- {_esc(n)} -->"

    # Open-pitch cards — private edition only.
    urgent = ""
    if not public:
        for p in pitches_by_date.get(date, []):
            urgent += _render_urgent(p)
    elif excluded is not None:
        for p in pitches_by_date.get(date, []):
            excluded.append(f"open-pitch card '{p['title'][:60]}'")

    top_html = ""
    stories = day["top"]
    if public:
        kept = []
        for s in stories:
            srcs = s["sources"]
            if srcs and all(_is_careers(x["sid"]) for x in srcs):
                if excluded is not None:
                    excluded.append(f"top story '{s['headline'][:60]}' (careers-only)")
                continue
            kept.append(s)
        stories = kept
    if stories:
        top_html = '<div class="eyebrow">Top stories</div>'
        for s in stories:
            top_html += _render_story(s)

    beats_html = _render_beats(day["beats"], public=public, excluded=excluded)

    # Mesh health — private edition only.
    if public:
        mesh_html = ""
        if excluded is not None and day["mesh"]:
            excluded.append("mesh-health section")
    else:
        mesh_html = _render_mesh(day["mesh"])

    pass_html = ""
    for blk in day["passthrough"]:
        inner = "".join(f"<p>{_md_inline(x)}</p>" for x in blk["body"])
        pass_html += (f'<div class="passthrough"><p class="pt-title">'
                      f'{_esc(blk["title"])}</p>{inner}</div>')

    stats = _esc(day["stats"]) or "&mdash;"
    return (
        f'<section class="day" data-month="{_attr(month_key)}" '
        f'data-week="{_attr(week_key)}">{note_html}'
        '<header class="day-head">'
        f'<h2 class="day-date">{_esc(pretty)}</h2>'
        f'<div class="day-stats">{stats}</div>'
        '</header>'
        f'{urgent}{top_html}{beats_html}{pass_html}{mesh_html}'
        '</section>')


def _render_chips(days):
    months, weeks = [], []
    seen_m, seen_w = set(), set()
    for d in days:
        if not d["date_obj"]:
            continue
        mk = d["date_obj"].strftime("%Y-%m")
        if mk not in seen_m:
            seen_m.add(mk)
            months.append((mk, mk))
        wmon = _week_monday(d["date_obj"])
        wk = wmon.strftime("%Y-%m-%d")
        if wk not in seen_w:
            seen_w.add(wk)
            weeks.append((wk, wmon.strftime("%b %-d") if os.name != "nt"
                          else wmon.strftime("%b %#d")))
    chips = ['<button class="chip" data-type="all" data-value="" '
             'aria-pressed="true">All</button>']
    for val, label in months:
        chips.append(f'<button class="chip" data-type="month" '
                     f'data-value="{_attr(val)}" aria-pressed="false">'
                     f'{_esc(label)}</button>')
    for val, label in weeks:
        chips.append(f'<button class="chip" data-type="week" '
                     f'data-value="{_attr(val)}" aria-pressed="false">'
                     f'wk {_esc(label)}</button>')
    return '<div class="chips" role="group" aria-label="Filter by month or week">' \
           + "".join(chips) + "</div>"


# --- static assets --------------------------------------------------------- #

_STYLE = """
:root{
  --bg:#F4F6F8; --surface:#FFFFFF; --ink:#16211C; --muted:#5A6B63;
  --line:#DCE4E0; --accent:#0F7B5F; --accent-ink:#FFFFFF;
  --urgent:#B25E09; --urgent-bg:#FBF1E4; --bar:rgba(244,246,248,.85);
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#0C1210; --surface:#131B18; --ink:#D9E2DC; --muted:#8FA39A;
    --line:#22302A; --accent:#3ECF9A; --accent-ink:#06120D;
    --urgent:#F0A34B; --urgent-bg:#2A2016; --bar:rgba(12,18,16,.85);
  }
}
:root[data-theme="dark"]{
  --bg:#0C1210; --surface:#131B18; --ink:#D9E2DC; --muted:#8FA39A;
  --line:#22302A; --accent:#3ECF9A; --accent-ink:#06120D;
  --urgent:#F0A34B; --urgent-bg:#2A2016; --bar:rgba(12,18,16,.85);
}
:root[data-theme="light"]{
  --bg:#F4F6F8; --surface:#FFFFFF; --ink:#16211C; --muted:#5A6B63;
  --line:#DCE4E0; --accent:#0F7B5F; --accent-ink:#FFFFFF;
  --urgent:#B25E09; --urgent-bg:#FBF1E4; --bar:rgba(244,246,248,.85);
}
*{box-sizing:border-box}
body,.ai-signal{margin:0;background:var(--bg);color:var(--ink);
  font-family:'Segoe UI',system-ui,sans-serif;line-height:1.6;
  -webkit-font-smoothing:antialiased;}
.wrap{max-width:720px;margin:0 auto;padding:0 20px 72px;}
.mono{font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;}
.serif{font-family:'Iowan Old Style','Palatino Linotype',Palatino,Georgia,serif;}
a{color:var(--accent);text-decoration:none;}
a:hover{text-decoration:underline;}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:3px;}

/* top bar */
.topbar{position:sticky;top:0;z-index:20;background:var(--bar);
  backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
  border-bottom:1px solid var(--line);}
.topbar-inner{max-width:720px;margin:0 auto;padding:12px 20px 10px;}
.wordmark{font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;
  font-weight:600;letter-spacing:.12em;font-size:.86rem;text-align:center;
  text-transform:uppercase;color:var(--ink);}
.cursor{color:var(--accent);animation:pulse 2s steps(1) infinite;}
@keyframes pulse{0%,49%{opacity:1}50%,100%{opacity:.15}}
@media (prefers-reduced-motion:reduce){.cursor{animation:none}}
.chips{display:flex;gap:6px;overflow-x:auto;margin-top:10px;padding-bottom:2px;
  scrollbar-width:thin;}
.chip{flex:0 0 auto;font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;
  font-size:.72rem;letter-spacing:.04em;padding:4px 10px;border-radius:999px;
  border:1px solid var(--line);background:var(--surface);color:var(--muted);
  cursor:pointer;white-space:nowrap;font-variant-numeric:tabular-nums;}
.chip[aria-pressed="true"]{background:var(--accent);color:var(--accent-ink);
  border-color:var(--accent);}

/* day section */
.day{padding-top:34px;}
.day-head{display:flex;justify-content:space-between;align-items:baseline;
  gap:16px;border-bottom:1px solid var(--line);padding-bottom:10px;
  margin-bottom:22px;}
.day-date{font-family:'Iowan Old Style','Palatino Linotype',Palatino,Georgia,serif;
  font-weight:500;font-size:1.5rem;margin:0;}
.day-stats{font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;
  font-size:.72rem;color:var(--muted);text-align:right;
  font-variant-numeric:tabular-nums;max-width:44%;}
@media (max-width:560px){
  .day-head{flex-direction:column;gap:4px;}
  .day-stats{text-align:left;max-width:100%;}
}

.eyebrow{font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;
  text-transform:uppercase;letter-spacing:.08em;font-size:.7rem;
  color:var(--muted);margin:26px 0 12px;}

/* urgent / open pitch */
.urgent{background:var(--urgent-bg);border-left:3px solid var(--urgent);
  border-radius:0 10px 10px 0;padding:14px 16px;margin:0 0 8px;}
.urgent-eyebrow{color:var(--urgent);margin:0 0 6px;}
.urgent-title{font-family:'Iowan Old Style','Palatino Linotype',Palatino,Georgia,serif;
  font-size:1.15rem;margin:0 0 6px;color:var(--ink);}
.urgent .why{margin:0 0 8px;color:var(--ink);font-size:.95rem;}
.urgent-link{font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;
  font-size:.74rem;color:var(--urgent);}

/* story card */
.story{background:var(--surface);border:1px solid var(--line);border-radius:12px;
  padding:18px 20px;margin:0 0 16px;}
.story h3{font-family:'Iowan Old Style','Palatino Linotype',Palatino,Georgia,serif;
  font-weight:600;font-size:clamp(1.35rem,4.5vw,1.75rem);line-height:1.22;
  text-wrap:balance;margin:0 0 10px;}
.story p{margin:0 0 10px;max-width:68ch;}
.story p:last-of-type{margin-bottom:0;}

/* details / sources */
.deeper{margin-top:12px;border-top:1px solid var(--line);padding-top:10px;}
.deeper>summary,.mesh>summary{list-style:none;cursor:pointer;
  font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;font-size:.75rem;
  letter-spacing:.04em;color:var(--muted);display:flex;align-items:center;gap:8px;}
.deeper>summary::-webkit-details-marker,
.mesh>summary::-webkit-details-marker{display:none;}
.deeper>summary::before,.mesh>summary::before{content:"\\25B8";
  color:var(--accent);transition:transform .18s ease;display:inline-block;}
.deeper[open]>summary::before,.mesh[open]>summary::before{transform:rotate(90deg);}
@media (prefers-reduced-motion:reduce){
  .deeper>summary::before,.mesh>summary::before{transition:none;}
}
.src-list{list-style:none;margin:12px 0 0;padding:0;}
.src{display:flex;align-items:baseline;gap:10px;padding:5px 0;flex-wrap:wrap;}
.sid{font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;font-size:.66rem;
  background:var(--line);color:var(--muted);padding:2px 6px;border-radius:5px;
  letter-spacing:.02em;white-space:nowrap;}
.src-title{flex:1;min-width:60%;}
.ts{font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;font-size:.68rem;
  color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap;}

/* by beat */
.beats{display:flex;flex-direction:column;gap:16px;}
.beat-name{font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;
  font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
  background:var(--surface);border:1px solid var(--line);border-radius:6px;
  padding:2px 8px;display:inline-block;margin-bottom:8px;}
.beat-items{list-style:none;margin:0;padding:0;}
.beat-item{padding:5px 0;border-bottom:1px solid var(--line);
  display:flex;gap:10px;align-items:baseline;justify-content:space-between;}
.beat-item:last-child{border-bottom:none;}
.beat-item a,.beat-item{max-width:68ch;}

/* mesh + passthrough */
.mesh{margin-top:30px;}
.mesh-body{color:var(--muted);font-size:.85rem;margin-top:10px;}
.mesh-body code,.story code,.beat-item code,.mesh-body strong{
  font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;font-size:.9em;}
.mesh-body ul{margin:6px 0;padding-left:18px;}
.passthrough{color:var(--muted);font-size:.9rem;margin:12px 0;}
.pt-title{font-weight:600;}

/* footer */
.foot{margin-top:52px;padding-top:16px;border-top:1px solid var(--line);
  font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;font-size:.72rem;
  color:var(--muted);font-variant-numeric:tabular-nums;}
"""

_SCRIPT = """
(function(){
  var chips=document.querySelectorAll('.chip');
  var days=document.querySelectorAll('.day');
  function apply(type,value){
    days.forEach(function(d){
      var show = type==='all' || d.dataset[type]===value;
      d.hidden = !show;
    });
  }
  chips.forEach(function(c){
    c.addEventListener('click',function(){
      chips.forEach(function(x){x.setAttribute('aria-pressed','false');});
      c.setAttribute('aria-pressed','true');
      apply(c.dataset.type, c.dataset.value);
    });
  });
})();
"""


def _render_body(days, pitches_by_date, generated, public=False, excluded=None):
    chips = _render_chips(days)
    day_html = "".join(
        _render_day(d, pitches_by_date, public=public, excluded=excluded)
        for d in days)
    if not days:
        day_html = ('<section class="day"><p class="passthrough">'
                    'No digests found yet.</p></section>')
    if public:
        # Attribution is welcome (author byline + profile link); what's barred is
        # tailoring the SYSTEM around the editor - standing rule in signaldesk CLAUDE.md.
        footer = (f'<footer class="foot"><a href="{PUBLIC_SITE_URL}">AI SIGNAL</a> '
                  '&mdash; a daily curated brief on AI infrastructure '
                  '&middot; curated by Hari Yelesetty</footer>')
    else:
        footer = (f'<footer class="foot">Generated {_esc(generated)} '
                  '&middot; signaldesk &middot; private</footer>')
    wordmark = ('<div class="wordmark">AI SIGNAL '
                '<span class="cursor">▮</span></div>')
    return (
        '<div class="ai-signal">'
        f'<div class="topbar"><div class="topbar-inner">{wordmark}{chips}</div></div>'
        f'<main class="wrap">{day_html}{footer}</main>'
        '</div>')


def render_page(days, pitches_by_date, generated, public=False, excluded=None):
    """Render a full standalone document. `public=True` produces the sanitized
    GitHub Pages edition (no pitches / careers / target-employers / mesh, and a
    public footer). One body/style/script; only content + shell metadata differ."""
    body = _render_body(days, pitches_by_date, generated,
                        public=public, excluded=excluded)
    style = f"<style>{_STYLE}</style>"
    script = f"<script>{_SCRIPT}</script>"
    head = ['<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">']
    if public:
        head.append('<meta name="description" content="A daily curated brief on '
                    'AI infrastructure — model releases, inference engines, '
                    'open weights, hardware.">')
    head.append('<title>AI Signal</title>')
    head_html = "\n".join(head)
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n'
        f"{head_html}\n"
        f"{style}\n</head>\n<body>\n{body}\n{script}\n</body>\n</html>\n")


# --------------------------------------------------------------------------- #
# Build                                                                        #
# --------------------------------------------------------------------------- #

def _collect_days(cfg):
    days = []
    for path in glob.glob(os.path.join(cfg.digests_dir, "*.md")):
        name = os.path.basename(path)
        if name.lower() in ("index.md", "readme.md"):
            continue
        days.append(parse_digest(path))
    # newest first; undated sink to the bottom
    days.sort(key=lambda d: (d["date_obj"] or datetime.min), reverse=True)
    return days


def _collect_proposed_pitches(cfg):
    by_date = {}
    if not os.path.isdir(cfg.pitches_dir):
        return by_date
    for path in sorted(glob.glob(os.path.join(cfg.pitches_dir, "*.md"))):
        if os.path.basename(path).lower() == "readme.md":
            continue
        p = parse_pitch(path)
        if p["status"] == "proposed" and p["date"]:
            by_date.setdefault(p["date"], []).append(p)
    return by_date


def build_site(cfg):
    """Parse all digests + proposed pitches and write BOTH editions:
      - site/index.html         full PRIVATE edition (everything).
      - site/public/index.html  sanitized PUBLIC edition (GitHub Pages).
    Returns (index_path, public_path, excluded) where `excluded` lists what the
    public sanitizer dropped this build (for eyeballing)."""
    days = _collect_days(cfg)
    pitches_by_date = _collect_proposed_pitches(cfg)
    generated = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")

    site_dir = os.path.join(cfg.signaldesk_dir, "site")
    public_dir = os.path.join(site_dir, "public")
    os.makedirs(public_dir, exist_ok=True)
    index_path = os.path.join(site_dir, "index.html")
    public_path = os.path.join(public_dir, "index.html")

    with open(index_path, "w", encoding="utf-8") as f:
        f.write(render_page(days, pitches_by_date, generated, public=False))

    excluded = []
    with open(public_path, "w", encoding="utf-8") as f:
        f.write(render_page(days, pitches_by_date, generated,
                            public=True, excluded=excluded))
    return index_path, public_path, excluded
