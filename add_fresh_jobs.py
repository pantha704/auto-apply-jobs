#!/usr/bin/env python3
"""Add fresh jobs with recency priority: 1h window > 24h > older.
Usage: python3 add_fresh_jobs.py <json> [--prio-bump N] [--portal X]
"""
import json, os, re, sqlite3, sys
from datetime import datetime, timezone

from title_filter import is_tech_title

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "apply_queue.db")

def canonical(url):
    if not url: return ""
    u = url.strip()
    u = re.sub(r"(\?|&)(trackingId|refId|trk|utm_[a-z]+|from|position|pageNum|gclid|fbclid|ref)[^&]*", "", u)
    u = re.sub(r"[?&]+$", "", u)
    u = u.rstrip("/")
    if "x.com" in u or "twitter.com" in u:
        u = re.sub(r"(x\.com|twitter\.com)/[^/]+/status/", "x.com/status/", u)
    return u

PRIO_BUMP = int(sys.argv[sys.argv.index("--prio-bump") + 1]) if "--prio-bump" in sys.argv else 0
PORTAL_OVERRIDE = sys.argv[sys.argv.index("--portal") + 1] if "--portal" in sys.argv else None
FRESH_FLAG = "--fresh" in sys.argv

def main():
    conn = sqlite3.connect(DB)
    existing = {canonical(r[0]) for r in conn.execute("SELECT url FROM jobs")}

    applied = set()
    if os.path.exists(os.path.join(HERE, "applications_log.tsv")):
        for line in open(os.path.join(HERE, "applications_log.tsv")):
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 5 and parts[0] != "time":
                applied.add(parts[4].strip())

    SEN = re.compile(r"\b(senior|lead|principal|staff|manager|director|head|architect|vp|chief|sr\.?)\b", re.I)
    added = 0
    for path in sys.argv[1:]:
        if path.startswith("--"):
            continue
        if not os.path.exists(path):
            print(f"missing: {path}")
            continue
        data = json.load(open(path))
        jobs = data.get("jobs", []) if isinstance(data, dict) else data
        for j in jobs:
            link = j.get("link") or j.get("url") or ""
            c = canonical(link)
            if not c or c in existing:
                continue
            if c in applied or c.rstrip("/") in {a.rstrip("/") for a in applied}:
                continue
            title = (j.get("title") or "")[:120]
            source = (j.get("source") or "linkedin") if PORTAL_OVERRIDE is None else PORTAL_OVERRIDE
            portal = source
            prio = 2 + PRIO_BUMP
            scopes = j.get("scopes", [])
            loc = (j.get("location") or "").lower()
            now = datetime.now(timezone.utc)
            fetched_at = now.isoformat()
            posted_raw = (j.get("date") or "").strip()
            posted_at, age_h = posted_raw, None
            if posted_raw:
                try:
                    dt = datetime.fromisoformat(posted_raw.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    age_h = (now - dt).total_seconds() / 3600
                except ValueError:
                    age_h = None
            if "wellfound" in source:
                portal, prio = "wellfound", 6 + PRIO_BUMP
            elif source == "linkedin":
                if SEN.search(title):
                    continue
                # --fresh: the caller (hourly harvest) already pre-filtered to fresh postings;
                # the coarse day-granularity 'date' field makes them look >24h old otherwise,
                # which was silently starving the LinkedIn queue despite a full harvest.
                if FRESH_FLAG:
                    pass
                elif age_h is not None and age_h > 24:
                    continue  # older than 24h goes out the window
                if "kolkata" in loc or "india_remote" in scopes or j.get("is_kolkata"):
                    prio = 4 + PRIO_BUMP
                elif "remote" in loc or "remote" in scopes:
                    prio = 2 + PRIO_BUMP
                else:
                    continue
                # freshness: <1h first choice, <24h last resort
                if age_h is not None:
                    prio += 6 if age_h <= 1 else 4 if age_h <= 6 else 2 if age_h <= 12 else 0
            elif source == "external":
                prio = 1 + PRIO_BUMP
            if not is_tech_title(title, source):
                continue
            jid = f"{source}-{abs(hash(c))}"
            conn.execute("INSERT OR IGNORE INTO jobs (id, portal, url, title, source, status, claimed_by, result, prio, posted_at, fetched_at) VALUES (?,?,?,?,?, 'pending', NULL, NULL, ?, ?, ?)",
                         (jid, portal, link, title, source, prio, posted_at or None, fetched_at))
            existing.add(c)
            added += 1
    conn.commit()
    print(f"added {added} new jobs (prio_bump={PRIO_BUMP})")
    for r in conn.execute("SELECT portal, status, COUNT(*) FROM jobs GROUP BY portal, status ORDER BY portal"):
        print(" ", r)
    conn.close()

if __name__ == "__main__":
    main()
