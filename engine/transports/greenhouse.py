"""Greenhouse jobs transport — one item per NEW posting vs the previous snapshot.

Greenhouse boards expose structured JSON (all current openings). We snapshot the
set of job ids each run; a run emits items only for ids not present in the prior
snapshot, then stores the new snapshot. First run establishes the baseline and
emits nothing (avoids a cold-start flood of every open role).
"""

from . import http
from ..util import to_iso


def fetch(source, since, cfg):
    store = cfg._runtime_store  # injected by collector
    data = http.get_json(source.url)
    jobs = data.get("jobs", []) if isinstance(data, dict) else []

    current = {}
    for j in jobs:
        jid = str(j.get("id"))
        current[jid] = {
            "id": jid,
            "title": j.get("title", ""),
            "url": j.get("absolute_url", ""),
            "updated_at": j.get("updated_at", ""),
            "location": (j.get("location") or {}).get("name", ""),
        }

    prev = store.last_snapshot(source.id)
    prev_ids = set()
    first_run = prev is None
    if prev:
        prev_ids = {e["id"] for e in prev.get("extracted", [])}

    items = []
    if not first_run:
        for jid, job in current.items():
            if jid in prev_ids:
                continue
            if not job["url"]:
                continue
            items.append({
                "url": job["url"],
                "title": f"New role: {job['title']}"
                         + (f" — {job['location']}" if job["location"] else ""),
                "published_utc": to_iso(job.get("updated_at", "")),
                "excerpt": f"New job posting at {source.id}: {job['title']}"
                           + (f" ({job['location']})" if job["location"] else ""),
                "beats": list(source.beats),
                "extra": {"greenhouse_id": jid, "location": job["location"]},
            })

    # store the new snapshot regardless (baseline on first run, diff basis after)
    extracted = list(current.values())
    normalized_hash = str(hash(frozenset(current.keys())))
    store.save_snapshot(source.id, source.url, normalized_hash, extracted)
    return items
