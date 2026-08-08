"""Site builder — the HTML edition of the signaldesk digest ("AI Signal").

Parses every `digests/YYYY-MM-DD.md` (the exact Markdown that `engine/digest.py`
writes) plus any `pitches/*.md` carrying `status: proposed`, and renders a
MULTI-PAGE site into `signaldesk/site/` (private) and `site/public/`
(sanitized GitHub Pages edition):

  - index.html            the INDEX: one line per day (date · top story ·
                          counts), month/week filter chips, newest first —
                          stays lightweight forever (365 days ≈ 365 rows).
  - days/YYYY-MM-DD.html  one self-contained page per digest day, with
                          prev/next navigation and a back-to-index link.

Editions share ONE renderer; a `public` flag sanitizes. Zero external requests:
no CDNs, no webfonts, no remote images — system font stacks and inline CSS/JS
only. The Markdown stays the record; this is the primary READ surface.

CLI: `python -m engine site`. Also invoked automatically after every digest
write (see engine/digest.py) so the page stays current.
"""

import glob
import html
import os
import re
import urllib.parse
from datetime import datetime

from .config import IST
# Mirror the writer's structure knowledge — share its constants rather than
# re-deriving them, so the parser tracks the writer if it changes.
from .digest import META_RE, TOP_N  # noqa: F401  (TOP_N documents the top/rest split)

# Personalization (footer byline, pitch-link base) comes from engine config —
# the engine ships zero personal data. See engine.config.example.json:
# public_footer_name / public_footer_url / workspace_repo_url.

# --- public-edition sanitizer -------------------------------------------------
# THE AUDIENCE CONTRACT — the public edition is a publication for strangers;
# the private edition is the desk's workbench. A stranger must never see:
#   * desk telemetry   — item/source/group counts, run timestamps, judged-pass
#                        stamps (kept in the private edition)
#   * internal ids     — collector sids (`hn-algolia`) become link domains
#                        (`news.ycombinator.com`); event ids / story threads
#                        are stripped outright
#   * editorial ops    — curation notes, drop accounting, mesh health, pitch
#                        cards, target-employer/careers items
#   * raw machinery    — markdown syntax, kebab-case beat slugs, "none" labels
# Anything reaching the public files must answer a reader's question, not
# describe the pipeline. Enforced by tests/test_render_robustness.py — extend
# the tests whenever a new element is added to either edition.
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
# Optional story-thread tag the collector appends to a by-beat bullet:
# " ↩ `evt-…`" — parsed off before the bullet shape is matched.
_BEAT_TAG_RE = re.compile(r"\s*↩\s*`(evt-[\w-]+)`\s*$")
# Story-thread machinery inside a TOP-STORY gist ("↩ UPDATE to `evt-…`", or a
# bare `evt-…` code token) — stripped from PUBLIC gists; the private edition
# keeps it (FLAGS 2026-07-19: leaked twice when left in by hand).
_STORY_TAG_RE = re.compile(
    r"↩\s*(?:UPDATE\s+to\s+)?`evt-[\w-]+`\.?|`evt-[\w-]+`|↩")
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

    # Reflow: judges hard-wrap long bullets across physical lines, splitting
    # [link](url) syntax so it can never render. A non-blank line that starts
    # with none of the structural prefixes and directly follows a "- " bullet
    # is that bullet's continuation — join it back.
    lines = []
    for raw in text.split("\n"):
        s = raw.strip()
        if lines and s and not s.startswith(("- ", "#", "<", "*", "_", "|")) \
                and lines[-1].lstrip().startswith("- "):
            lines[-1] = lines[-1].rstrip() + " " + s
        else:
            lines.append(raw)

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
                if head.strip().lower() in ("none", "(no ranked story)"):
                    head = _QUIET
                cur_story = {"headline": head, "gist": [], "sources": []}
                day["top"].append(cur_story)
            elif section == "beat":
                cur_beat = {"name": head, "items": []}
                day["beats"].append(cur_beat)
            elif section == "passthrough" and day["passthrough"]:
                day["passthrough"][-1]["body"].append(head)
            continue

        # An italic explanation directly under "## Top stories" with no story
        # yet = the quiet-day note; keep it (renders as the day's lede).
        if section == "top" and cur_story is None and stripped.startswith("_") \
                and stripped.endswith("_") and len(stripped) > 2:
            day["passthrough"].insert(0, {"title": "", "body": [stripped.strip("_")]})
            continue

        if section == "top" and cur_story is not None:
            if stripped.startswith("<details") \
                    or stripped.startswith("<summary") \
                    or stripped.startswith("</details") \
                    or stripped.startswith("</summary"):
                continue
            if not stripped:
                # A blank line inside a top story is a paragraph break — keep
                # it as a marker so _render_story can split the gist into
                # separate <p>s (FLAGS 2026-08-07: every gist was silently
                # collapsing into one merged paragraph because this blank
                # was dropped outright, which is also why the "↩ UPDATE to
                # `evt-…` (first seen…) — prior state: …" continuity line
                # never separated cleanly from the real gist text on the
                # public page).
                if cur_story["gist"] and cur_story["gist"][-1] != "":
                    cur_story["gist"].append("")
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
            tag_m = _BEAT_TAG_RE.search(stripped)
            story_id = tag_m.group(1) if tag_m else ""
            bullet = stripped[:tag_m.start()].rstrip() if tag_m else stripped
            bm = _BEAT_BULLET_RE.match(bullet)
            if bm:
                cur_beat["items"].append({
                    "title": bm.group(1).strip(), "sid": bm.group(2),
                    "url": bm.group(3), "story": story_id})
            else:
                cur_beat["items"].append({
                    "title": bullet[2:].strip(), "sid": "", "url": "",
                    "story": story_id})
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
        parts.append(f'<a href="{_attr(mo.group(2))}">'
                     f'{_md_bold_code(mo.group(1))}</a>')
        i = mo.end()
    parts.append(_md_bold_code(s[i:]))
    return "".join(parts)


def _md_bold_code(s):
    out = _esc(s)
    out = re.sub(r"`([^`]+)`", lambda m: f"<code>{m.group(1)}</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", lambda m: f"<strong>{m.group(1)}</strong>", out)
    return out


def _domain(url):
    """Reader-facing source attribution: the link's host, not our internal
    collector id (a stranger knows github.com, not `hn-algolia`)."""
    try:
        host = urllib.parse.urlparse(url).netloc
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _render_story(story, public=False):
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
        if public:
            # Desk machinery must never reach a stranger: drop the whole
            # continuity paragraph ("↩ UPDATE to `evt-…` (first seen …,
            # N prior items) — prior state: …") from public gists, not just
            # the tag token inside it (FLAGS 2026-08-07: the old tag-only
            # strip left the "(first seen…) — prior state: …" trailer
            # behind, leaking desk bookkeeping onto already-published public
            # pages). Also strip bare evt- code tokens from real gist text.
            if p.lstrip().startswith("↩") or "prior state:" in p.lower():
                continue
            p = _STORY_TAG_RE.sub("", p).strip()
            if not p:
                continue
        gist_html += f"<p>{_md_inline(p)}</p>"

    src_rows = ""
    for s in story["sources"]:
        tag = _domain(s["url"]) if public else s["sid"]
        src_rows += (
            '<li class="src">'
            f'<span class="sid">{_esc(tag)}</span>'
            f'<a class="src-title" href="{_attr(s["url"])}">{_esc(s["title"])}</a>'
            f'<span class="ts">{_esc(s["ts"])}</span>'
            '</li>')
    details = ""
    if src_rows:
        details = (
            '<details class="deeper"><summary>Sources &amp; depth</summary>'
            f'<ul class="src-list">{src_rows}</ul></details>')
    return (f'<article class="story"><h3>{head}</h3>{gist_html}{details}</article>')


# Internal beat ids → reader-facing labels (the digest Markdown keeps the
# internal names; only the HTML rendering translates).
_BEAT_LABELS = {
    "community-pulse": "From the community",
    "momentum-signal": "Momentum",
    "inference-serving": "Inference & serving",
    "frontier-releases": "Frontier releases",
    "open-weights": "Open weights",
    "fine-tuning": "Fine-tuning",
    "pricing-economics": "Pricing & economics",
    "multimodal-generation": "Multimodal generation",
    "mlops-llm": "MLOps",
    "uncategorized": "Elsewhere",
}


def _beat_label(name):
    key = (name or "").strip().lower()
    return _BEAT_LABELS.get(key, key.replace("-", " ").capitalize())


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
            # Trailing bare "[source-id]" (no url) = a sid chip, not a link.
            if not it["sid"]:
                tm = re.match(r"^(.*)\s+[—-]\s+\[([\w-]{2,40})\]$",
                              it["title"].strip())
                if tm:
                    it = dict(it, title=tm.group(1).strip(), sid=tm.group(2))
            title_has_md = bool(_LINK_RE.search(it["title"]))
            if it["url"] and not title_has_md:
                title = f'<a href="{_attr(it["url"])}">{_esc(it["title"])}</a>'
            elif not it["url"] and not title_has_md:
                # No link anywhere: a curation NOTE (what was dropped and
                # why) — the editor talking to the desk, NOT news. Private
                # edition renders it muted; the public reader never sees it.
                if public:
                    if excluded is not None:
                        excluded.append(f"curation note '{it['title'][:60]}'")
                else:
                    items += (f'<li class="beat-note">'
                              f'{_md_inline(it["title"])}</li>')
                continue
            else:
                # Judge-authored rich line: render its own markdown links;
                # never nest raw md inside another anchor.
                title = _md_inline(it["title"])
            if it["sid"]:
                # Public readers get the link's domain, not our collector id;
                # no url to derive one from → the title link is attribution
                # enough, drop the chip.
                tag = _domain(it["url"]) if public else it["sid"]
                if not tag:
                    chip = ""
                elif it["url"] and title_has_md:
                    chip = (f'<a class="sid" href="{_attr(it["url"])}">'
                            f'{_esc(tag)}</a>')
                else:
                    chip = f'<span class="sid">{_esc(tag)}</span>'
            else:
                chip = ""
            # Story-thread tag: desk machinery — private edition only.
            if it.get("story") and not public:
                chip += f' <span class="sid">↩ {_esc(it["story"])}</span>'
            items += f'<li class="beat-item">{title}{chip}</li>'
        if not items:
            continue
        rows += (
            '<div class="beat">'
            f'<div class="beat-name">{_esc(_beat_label(b["name"]))}</div>'
            f'<ul class="beat-items">{items}</ul></div>')
    if not rows:
        return ""
    label = "Also today" if public else "By beat"
    return (
        f'<div class="eyebrow">{label}</div>'
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


def _render_urgent(pitch, link_base=""):
    why = f'<p class="why">{_esc(pitch["whynow"])}</p>' if pitch["whynow"] else ""
    link_html = ""
    if link_base:
        link = f"{link_base}/{pitch['file']}"
        link_html = (f'<a class="urgent-link" href="{_attr(link)}">'
                     'View pitch file &rarr;</a>')
    return (
        '<aside class="urgent">'
        '<div class="eyebrow urgent-eyebrow">Open pitch</div>'
        f'<h3 class="urgent-title">{_esc(pitch["title"])}</h3>'
        f'{why}{link_html}'
        '</aside>')


def _render_day(day, pitches_by_date, public=False, excluded=None,
                pitch_link_base=""):
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
            urgent += _render_urgent(p, pitch_link_base)
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
            top_html += _render_story(s, public=public)

    beats_html = _render_beats(day["beats"], public=public, excluded=excluded)

    # Mesh health — private edition only.
    if public:
        mesh_html = ""
        if excluded is not None and day["mesh"]:
            excluded.append("mesh-health section")
    else:
        mesh_html = _render_mesh(day["mesh"])

    # The untitled passthrough block is the day's lede (e.g. the quiet-day
    # summary) — it opens the page, everything else closes it.
    lede_html = ""
    for blk in [b for b in day["passthrough"] if not b["title"]]:
        for x in blk["body"]:
            lede_html += f'<p class="lede">{_md_inline(x.strip().strip("_"))}</p>'

    pass_html = ""
    for blk in [b for b in day["passthrough"] if b["title"]]:
        # Internal ops sections (event ids, registry-patch notes) never reach
        # the public edition.
        if public and blk["title"].strip().lower() in ("story threads",):
            if excluded is not None:
                excluded.append(f"internal section '{blk['title']}'")
            continue
        inner = ""
        for x in blk["body"]:
            x = x.strip()
            if x.startswith("_") and x.endswith("_") and len(x) > 2:
                inner += f"<p><em>{_md_inline(x.strip('_'))}</em></p>"
            elif x.startswith("- "):
                inner += f'<li>{_md_inline(x[2:])}</li>'
            else:
                inner += f"<p>{_md_inline(x)}</p>"
        inner = re.sub(r"(?:<li>.*?</li>)+",
                       lambda m: f"<ul>{m.group(0)}</ul>", inner)
        title_html = (f'<p class="pt-title">{_esc(blk["title"])}</p>'
                      if blk["title"] else "")
        pass_html += f'<div class="passthrough">{title_html}{inner}</div>'

    # Collector telemetry ("47 items · 5 sources · run 09:09 IST") is desk
    # accounting — the private edition shows it, readers never see it.
    if public:
        stats_html = ""
        if excluded is not None and day["stats"]:
            excluded.append("stats line (collector telemetry)")
    else:
        stats_html = f'<div class="day-stats">{_esc(day["stats"]) or "&mdash;"}</div>'
    return (
        f'<section class="day" data-month="{_attr(month_key)}" '
        f'data-week="{_attr(week_key)}">{note_html}'
        '<header class="day-head">'
        f'<h2 class="day-date">{_esc(pretty)}</h2>'
        f'{stats_html}'
        '</header>'
        f'{lede_html}{urgent}{top_html}{beats_html}{pass_html}{mesh_html}'
        '</section>')


_QUIET = "Quiet day — scanned, nothing significant"


def _day_headline(day):
    """Best one-line headline for the index row."""
    head = ""
    if day["meta"] and day["meta"].get("top"):
        head = day["meta"]["top"]
    elif day["top"]:
        head = day["top"][0]["headline"]
    if head.strip().lower() in ("", "none", "(no ranked story)", "(untitled)",
                                "(no stories)"):
        return _QUIET
    return head


def _short_stats(day):
    if day["meta"]:
        return f'{day["meta"]["items"]} items · {day["meta"]["groups"]} groups'
    return day["stats"][:60] if day["stats"] else ""


def _render_index_rows(days, public=False):
    """One anchor row per day, newest first, carrying month/week data attrs
    for the chip filter. Links are relative: days/YYYY-MM-DD.html."""
    rows = ""
    for d in days:
        if not d["date_obj"]:
            continue
        date_obj = d["date_obj"]
        pretty = date_obj.strftime("%a, %b %-d, %Y") if os.name != "nt" \
            else date_obj.strftime("%a, %b %#d, %Y")
        mk = date_obj.strftime("%Y-%m")
        wk = _week_monday(date_obj).strftime("%Y-%m-%d")
        # Item/group counts are desk telemetry — private edition only.
        stats = ("" if public else
                 f'<span class="idx-stats">{_esc(_short_stats(d))}</span>')
        rows += (
            f'<a class="idx-row" href="days/{_attr(d["date"])}.html" '
            f'data-month="{_attr(mk)}" data-week="{_attr(wk)}">'
            f'<span class="idx-date">{_esc(pretty)}</span>'
            f'<span class="idx-head">{_esc(_day_headline(d))}</span>'
            f'{stats}'
            '</a>')
    if not rows:
        rows = '<p class="passthrough">No digests yet.</p>'
    return f'<div class="idx">{rows}</div>'


def _render_browse(days):
    """Date navigation for the index, grouped month -> week with counts.

    Replaces the flat chip bar, which put every month and a truncated handful
    of weeks in one undifferentiated row: it stopped being readable within a
    few weeks of dailies and silently hid the rest. Here every week is
    reachable because weeks nest inside their month, and only the newest month
    is expanded, so the control stays the same size as the archive grows.
    """
    months = []          # [(key, label, count, [(wkey, wlabel, wcount)])]
    for d in days:
        if not d["date_obj"]:
            continue
        mk = d["date_obj"].strftime("%Y-%m")
        wmon = _week_monday(d["date_obj"])
        wk = wmon.strftime("%Y-%m-%d")
        wlabel = (wmon.strftime("%b %-d") if os.name != "nt"
                  else wmon.strftime("%b %#d"))
        mlabel = d["date_obj"].strftime("%B %Y")
        m = next((x for x in months if x[0] == mk), None)
        if m is None:
            m = (mk, mlabel, [0], [])
            months.append(m)
        m[2][0] += 1
        w = next((x for x in m[3] if x[0] == wk), None)
        if w is None:
            m[3].append((wk, wlabel, [1]))
        else:
            w[2][0] += 1

    total = sum(m[2][0] for m in months)
    if not total:
        return ""

    def btn(kind, value, label, count, pressed=False):
        return (f'<button class="br-b" type="button" data-type="{_attr(kind)}" '
                f'data-value="{_attr(value)}" '
                f'aria-pressed="{"true" if pressed else "false"}">'
                f'<span>{_esc(label)}</span>'
                f'<span class="br-n">{count}</span></button>')

    out = [btn("all", "", "All editions", total, pressed=True)]
    for i, (mk, mlabel, mcount, weeks) in enumerate(months):
        rows = [f'<div class="br-wk">{btn("month", mk, "Whole month", mcount[0])}']
        for wk, wlabel, wcount in weeks:
            rows.append(btn("week", wk, f"Week of {wlabel}", wcount[0]))
        rows.append("</div>")
        out.append(
            f'<details class="br-mon"{" open" if i == 0 else ""}>'
            f'<summary>{_esc(mlabel)}<span class="br-n">{mcount[0]}</span>'
            f'</summary>{"".join(rows)}</details>')
    out.append('<p class="br-none" hidden>No editions in that range.</p>')
    return ('<div class="sidecard"><h2 class="side-h">Browse</h2>'
            f'<nav class="browse" aria-label="Filter editions by month or week">'
            f'{"".join(out)}</nav></div>')


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
    # A year of dailies would mean 52 week chips — keep the bar usable:
    # months carry deep history, week chips only for the recent stretch.
    weeks = weeks[:6]
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
.wrap{max-width:860px;margin:0 auto;padding:0 24px 64px;}
.mono{font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;}
.serif{font-family:'Iowan Old Style','Palatino Linotype',Palatino,Georgia,serif;}
a{color:var(--accent);text-decoration:none;}
a:hover{text-decoration:underline;}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:3px;}

/* top bar */
.topbar{position:sticky;top:0;z-index:20;background:var(--bar);
  backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
  border-bottom:1px solid var(--line);}
.topbar-inner{max-width:860px;margin:0 auto;padding:12px 24px 10px;}
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
.src-list{list-style:none;margin:12px 0 0;padding:0;counter-reset:src;}
.src{display:flex;align-items:baseline;gap:10px;padding:5px 0 5px 26px;
  flex-wrap:wrap;counter-increment:src;position:relative;}
.src::before{content:counter(src) ".";position:absolute;left:0;top:5px;
  font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;font-size:.7rem;
  color:var(--muted);font-variant-numeric:tabular-nums;}
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
.beat-item{padding:6px 0 6px 16px;border-bottom:1px solid var(--line);
  position:relative;display:block;max-width:none;}
.beat-item::before{content:"\\25B8";position:absolute;left:0;top:6px;
  color:var(--accent);font-size:.8rem;}
.beat-item .sid{margin-left:8px;}
.beat-note{padding:5px 0 5px 16px;color:var(--muted);font-size:.85rem;
  border-bottom:1px solid var(--line);}
.beat-note:last-child{border-bottom:none;}
.beat-item:last-child{border-bottom:none;}

/* mesh + passthrough */
.mesh{margin-top:30px;}
.mesh-body{color:var(--muted);font-size:.85rem;margin-top:10px;}
.mesh-body code,.story code,.beat-item code,.mesh-body strong{
  font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;font-size:.9em;}
.mesh-body ul{margin:6px 0;padding-left:18px;}
.passthrough{color:var(--muted);font-size:.9rem;margin:12px 0;}
.lede{font-family:'Iowan Old Style','Palatino Linotype',Palatino,Georgia,serif;
  font-size:1.08rem;color:var(--ink);max-width:68ch;margin:4px 0 6px;}
.pt-title{font-weight:600;}

/* footer */
.foot{margin-top:52px;padding-top:16px;border-top:1px solid var(--line);
  font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;font-size:.72rem;
  color:var(--muted);font-variant-numeric:tabular-nums;}
.topnav{margin-top:8px;font-size:.85rem;}
.topnav a{color:var(--muted);text-decoration:none;}
.topnav a:hover{color:var(--accent);}
.foot-links{margin-top:6px;}
.foot-links a{color:var(--muted);}
.foot-links a:hover{color:var(--accent);}

/* index rows */
.idx{display:flex;flex-direction:column;margin-top:26px;}
.idx-row{display:grid;grid-template-columns:150px 1fr auto;gap:14px;
  align-items:baseline;padding:13px 4px;border-bottom:1px solid var(--line);
  color:var(--ink);}
.idx-row:hover{text-decoration:none;background:var(--surface);}
.idx-row[hidden]{display:none;}
.idx-date{font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;
  font-size:.74rem;color:var(--muted);font-variant-numeric:tabular-nums;
  white-space:nowrap;}
.idx-head{font-family:'Iowan Old Style','Palatino Linotype',Palatino,Georgia,serif;
  font-size:1.05rem;line-height:1.35;}
.idx-row:hover .idx-head{color:var(--accent);}
.idx-stats{font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;
  font-size:.68rem;color:var(--muted);font-variant-numeric:tabular-nums;
  white-space:nowrap;}
@media (max-width:560px){
  .idx-row{grid-template-columns:1fr;gap:3px;}
  .idx-stats{display:none;}
}

/* index: wide two-column layout (feed + metadata sidebar) */
.wrap.wide{max-width:1200px;}
.topbar-inner.wide{max-width:1200px;}
.layout{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:56px;
  align-items:start;padding-top:26px;}
.layout .idx{margin-top:0;}
.side{display:flex;flex-direction:column;gap:16px;}
.sidecard{background:var(--surface);border:1px solid var(--line);
  border-radius:12px;padding:14px 18px 16px;}
.side-h{font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;
  text-transform:uppercase;letter-spacing:.08em;font-size:.68rem;
  color:var(--muted);font-weight:600;margin:0 0 8px;}
.side-about{margin:0;font-size:.9rem;color:var(--muted);line-height:1.55;}
.side-list{list-style:none;margin:0;padding:0;}
.side-list li{border-bottom:1px solid var(--line);}
.side-list li:last-child{border-bottom:none;}
.side-list a{display:block;padding:6px 0;color:var(--ink);font-size:.9rem;}
.side-list a:hover{color:var(--accent);text-decoration:none;}
.browse{font-size:.9rem;}
.br-b{display:flex;width:100%;align-items:baseline;gap:8px;background:none;
  border:0;padding:5px 6px;border-radius:7px;cursor:pointer;color:var(--ink);
  font:inherit;text-align:left;}
.br-b:hover{background:var(--code-bg);}
.br-b[aria-pressed="true"]{background:var(--accent);color:var(--accent-ink);}
.br-b[aria-pressed="true"] .br-n{color:inherit;opacity:.8;}
.br-n{margin-left:auto;font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;
  font-size:.72rem;color:var(--muted);font-variant-numeric:tabular-nums;}
.br-mon{border-top:1px solid var(--line);margin-top:4px;padding-top:4px;}
.br-mon:first-of-type{border-top:0;margin-top:0;}
.br-mon>summary{list-style:none;cursor:pointer;display:flex;align-items:baseline;
  gap:8px;padding:5px 6px;border-radius:7px;
  font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;font-size:.78rem;
  color:var(--muted);}
.br-mon>summary::-webkit-details-marker{display:none;}
.br-mon>summary::before{content:"\\25B8";transition:transform .15s ease;
  display:inline-block;color:var(--accent);
  /* the summary is monospace, and Consolas has no U+25B8 - it renders as
     tofu unless the marker gets a font stack that actually carries it */
  font-family:'Segoe UI Symbol','Apple Symbols',system-ui,sans-serif;}
.br-mon[open]>summary::before{transform:rotate(90deg);}
.br-mon>summary:hover{background:var(--code-bg);color:var(--ink);}
.br-wk{margin:2px 0 6px;padding-left:12px;}
.br-wk .br-b{font-size:.85rem;}
.br-none{margin:8px 0 0;font-size:.82rem;color:var(--muted);}
@media (prefers-reduced-motion:reduce){.br-mon>summary::before{transition:none;}}
.side-stats{display:grid;grid-template-columns:1fr auto;gap:4px 14px;margin:0;}
.side-stats dt{color:var(--muted);font-size:.84rem;}
.side-stats dd{margin:0;font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;
  font-size:.78rem;text-align:right;font-variant-numeric:tabular-nums;
  align-self:baseline;}
@media (max-width:960px){
  .layout{grid-template-columns:1fr;gap:12px;}
  .side{margin-top:30px;}
}

/* day-page nav */
.daynav{display:flex;justify-content:space-between;gap:12px;margin-top:34px;
  font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;font-size:.76rem;}
.backlink{font-family:ui-monospace,'Cascadia Mono',Consolas,monospace;
  font-size:.74rem;}
"""

_SCRIPT = """
(function(){
  var btns=document.querySelectorAll('.br-b');
  var days=document.querySelectorAll('.idx-row');
  var none=document.querySelector('.br-none');
  if(!btns.length||!days.length){return;}
  function apply(type,value){
    var shown=0;
    days.forEach(function(d){
      var show = type==='all' || d.dataset[type]===value;
      d.hidden = !show;
      if(show){shown++;}
    });
    if(none){none.hidden = shown>0;}
  }
  btns.forEach(function(b){
    b.addEventListener('click',function(){
      btns.forEach(function(x){x.setAttribute('aria-pressed','false');});
      b.setAttribute('aria-pressed','true');
      apply(b.dataset.type, b.dataset.value);
      // Opening a week inside a collapsed month should leave it open.
      var mon=b.closest('details.br-mon');
      if(mon){mon.open=true;}
    });
  });
})();
"""


def _footer(generated, public, cfg=None):
    if public:
        name = getattr(cfg, "public_footer_name", "") if cfg else ""
        url = getattr(cfg, "public_footer_url", "") if cfg else ""
        tagline = (getattr(cfg, "public_footer_tagline", "") if cfg else "") \
            or "a daily curated brief"
        links = getattr(cfg, "public_footer_links", []) if cfg else []
        if name and url:
            byline = (f' &middot; curated by '
                      f'<a href="{_attr(url)}">{_esc(name)}</a>')
        elif name:
            byline = f" &middot; curated by {_esc(name)}"
        else:
            byline = ""
        contact = ""
        if links:
            row = " &middot; ".join(
                f'<a href="{_attr(l["url"])}">{_esc(l["label"])}</a>'
                for l in links)
            contact = f'<div class="foot-links">{row}</div>'
        return (f'<footer class="foot">AI SIGNAL '
                f'&mdash; {_esc(tagline)}{byline}{contact}</footer>')
    return (f'<footer class="foot">Generated {_esc(generated)} '
            '&middot; private edition</footer>')


def _shell(body, public, title, script="", cfg=None):
    style = f"<style>{_STYLE}</style>"
    head = ['<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">']
    if public:
        tagline = (getattr(cfg, "public_footer_tagline", "") if cfg else "") \
            or "a daily curated AI news brief"
        desc = "AI Signal — " + tagline
        head.append(f'<meta name="description" content="{_attr(desc)}">')
    head.append(f'<title>{_esc(title)}</title>')
    head_html = "\n".join(head)
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n'
        f"{head_html}\n"
        f"{style}\n</head>\n<body>\n{body}\n{script}\n</body>\n</html>\n")


def _fmt_pretty(date_obj, fmt="%b %-d, %Y"):
    if os.name == "nt":
        fmt = fmt.replace("%-d", "%#d")
    return date_obj.strftime(fmt)


def _render_index_sidebar(days, cfg):
    """Metadata sidebar for the index (PyPI/GitHub-repo style): about, links,
    archive stats. Everything personal comes from cfg — no cfg, no residue.
    This is the single home of the site's links on the index page."""
    cards = []
    tagline = (getattr(cfg, "public_footer_tagline", "") if cfg else "") \
        or "a daily curated AI news brief"
    name = getattr(cfg, "public_footer_name", "") if cfg else ""
    url = getattr(cfg, "public_footer_url", "") if cfg else ""
    if name and url:
        byline = f' By <a href="{_attr(url)}">{_esc(name)}</a>.'
    elif name:
        byline = f" By {_esc(name)}."
    else:
        byline = ""
    cards.append('<div class="sidecard"><h2 class="side-h">About</h2>'
                 f'<p class="side-about">AI SIGNAL &mdash; {_esc(tagline)}.'
                 f'{byline}</p></div>')
    cards.append(_render_browse(days))

    # Two distinct link groups, deliberately NOT merged. "Explore" is the
    # publication's own work a reader might want next; "Author" is who wrote it.
    # They used to render as one list, which filed a personal profile and a
    # LinkedIn page alongside the benchmark as though they were the same kind
    # of thing.
    def _links(key):
        seen, items = set(), []
        for l in (getattr(cfg, key, []) if cfg else []):
            if l["url"] in seen:
                continue
            seen.add(l["url"])
            items.append(
                f'<li><a href="{_attr(l["url"])}">{_esc(l["label"])}</a></li>')
        return items

    for title, key in (("Explore", "public_nav_links"),
                       ("Author", "public_author_links")):
        items = _links(key)
        if items:
            cards.append(f'<div class="sidecard"><h2 class="side-h">{title}</h2>'
                         f'<ul class="side-list">{"".join(items)}</ul></div>')
    dated = [d for d in days if d["date_obj"]]
    if dated:
        rows = (f'<dt>Editions</dt><dd>{len(dated)}</dd>'
                f'<dt>Latest</dt><dd>{_esc(_fmt_pretty(dated[0]["date_obj"]))}</dd>'
                f'<dt>Since</dt><dd>{_esc(_fmt_pretty(dated[-1]["date_obj"]))}</dd>')
        cards.append('<div class="sidecard"><h2 class="side-h">Archive</h2>'
                     f'<dl class="side-stats">{rows}</dl></div>')
    return f'<aside class="side">{"".join(cards)}</aside>'


def render_index_page(days, generated, public=False, cfg=None):
    """The INDEX: wordmark + month/week chips up top, then a two-column layout —
    day rows on the left, metadata sidebar (about/links/archive) on the right.
    Scales linearly in rows, not content. The sidebar is the one home for the
    site's links here, so the public index carries no duplicate footer."""
    wordmark = ('<div class="wordmark">AI SIGNAL '
                '<span class="cursor">▮</span></div>')
    foot = "" if public else _footer(generated, public, cfg)
    body = (
        '<div class="ai-signal">'
        f'<div class="topbar"><div class="topbar-inner wide">{wordmark}'
        '</div></div>'
        '<main class="wrap wide"><div class="layout">'
        f'<div>{_render_index_rows(days, public=public)}</div>'
        f'{_render_index_sidebar(days, cfg)}'
        f'</div>{foot}</main></div>')
    return _shell(body, public, "AI Signal", f"<script>{_SCRIPT}</script>",
                  cfg=cfg)


def render_day_page(day, prev_day, next_day, pitches_by_date, generated,
                    public=False, excluded=None, cfg=None):
    """One digest day as its own page, with back-to-index + prev/next nav."""
    wordmark = ('<div class="wordmark"><a href="../index.html" '
                'style="color:inherit">AI SIGNAL</a> '
                '<span class="cursor">▮</span></div>')
    nav = '<nav class="daynav">'
    nav += (f'<a href="{_attr(prev_day["date"])}.html">&larr; {_esc(prev_day["date"])}</a>'
            if prev_day else '<span></span>')
    nav += '<a class="backlink" href="../index.html">all days</a>'
    nav += (f'<a href="{_attr(next_day["date"])}.html">{_esc(next_day["date"])} &rarr;</a>'
            if next_day else '<span></span>')
    nav += '</nav>'
    repo_url = getattr(cfg, "workspace_repo_url", "") if cfg else ""
    link_base = f"{repo_url}/blob/main/pitches" if repo_url else ""
    body = (
        '<div class="ai-signal">'
        f'<div class="topbar"><div class="topbar-inner">{wordmark}</div></div>'
        '<main class="wrap">'
        f'{_render_day(day, pitches_by_date, public=public, excluded=excluded, pitch_link_base=link_base)}'
        f'{nav}{_footer(generated, public, cfg)}</main></div>')
    return _shell(body, public, f'AI Signal — {day["date"]}', cfg=cfg)


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


def _write_edition(root, days, pitches_by_date, generated, public, excluded,
                   cfg=None):
    """Write index.html + days/*.html under `root`. Stale day pages (deleted
    digests) are removed — the days/ dir is fully generator-owned."""
    days_dir = os.path.join(root, "days")
    os.makedirs(days_dir, exist_ok=True)
    for old in glob.glob(os.path.join(days_dir, "*.html")):
        os.remove(old)
    dated = [d for d in days if d["date_obj"]]  # newest first (pre-sorted)
    for i, d in enumerate(dated):
        prev_day = dated[i + 1] if i + 1 < len(dated) else None   # older
        next_day = dated[i - 1] if i > 0 else None                # newer
        page = render_day_page(d, prev_day, next_day, pitches_by_date,
                               generated, public=public, excluded=excluded,
                               cfg=cfg)
        with open(os.path.join(days_dir, f'{d["date"]}.html'),
                  "w", encoding="utf-8") as f:
            f.write(page)
    index_path = os.path.join(root, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(render_index_page(days, generated, public=public, cfg=cfg))
    return index_path, len(dated)


def build_site(cfg):
    """Parse all digests + proposed pitches and write BOTH multi-page editions:
      - site/index.html + site/days/*.html            full PRIVATE edition.
      - site/public/index.html + site/public/days/*   sanitized PUBLIC edition
        (GitHub Pages — publish by copying the whole site/public/ tree).
    Returns (index_path, public_path, excluded) where `excluded` lists what the
    public sanitizer dropped this build (for eyeballing)."""
    days = _collect_days(cfg)
    pitches_by_date = _collect_proposed_pitches(cfg)
    generated = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")

    site_dir = os.path.join(cfg.signaldesk_dir, "site")
    public_dir = os.path.join(site_dir, "public")

    index_path, _n = _write_edition(site_dir, days, pitches_by_date,
                                    generated, public=False, excluded=None,
                                    cfg=cfg)
    excluded = []
    public_path, _n = _write_edition(public_dir, days, pitches_by_date,
                                     generated, public=True, excluded=excluded,
                                     cfg=cfg)
    return index_path, public_path, excluded
