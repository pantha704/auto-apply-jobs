import json, os, sqlite3, sys
from datetime import datetime, timezone

from job_identity import canonical_url as canonical, stable_job_id
from title_filter import is_tech_title

HERE = "/home/ubuntu/job_hunt_linkedin"
DB = os.getenv("JOBHUNT_QUEUE_DB", os.path.join(HERE, "apply_queue.db"))
WELLFOUND_SEEN_DB = os.getenv("WELLFOUND_SEEN_DB", os.path.join(HERE, ".wellfound_fresh_seen.db"))

conn = sqlite3.connect(DB)
existing = {canonical(r[0]) for r in conn.execute("SELECT url FROM jobs")}
applied = set()
if os.path.exists(os.path.join(HERE, "applications_log.tsv")):
    for line in open(os.path.join(HERE, "applications_log.tsv")):
        p = line.rstrip("\n").split("\t")
        if len(p) >= 5 and p[0] != "time":
            applied.add(canonical(p[4]))
try:
    applied.update(canonical(r[0]) for r in conn.execute("SELECT url FROM applications"))
except sqlite3.OperationalError:
    pass

SRC = sys.argv[1] if len(sys.argv) > 1 else "/tmp/site_collect.json"
data = json.load(open(SRC))
added = 0
wf_seen = set()
PRIO = {"wellfound": 6, "wellfound_us": 6, "wellfound_fresh": 6, "internshala": 5, "naukri": 4, "yc": 5, "weworkremotely": 1}
EXTERNAL_SOURCES = {"naukri", "weworkremotely", "himalayas"}
fetched_at = datetime.now(timezone.utc).isoformat()
for s in data:
    site = s.get("site", "?")
    portal = "wellfound" if site.startswith("wellfound") else "external" if site in EXTERNAL_SOURCES else site
    prio = PRIO.get(site, 3)
    for j in s.get("jobs", []):
        link = j.get("link") or j.get("url") or ""
        if site == "wellfound_fresh" and link:
            wf_seen.add(link)
        c = canonical(link)
        if not c or c in existing:
            continue
        if c in applied or c.rstrip("/") in {a.rstrip("/") for a in applied}:
            continue
        title = (j.get("title") or "")[:120]
        if not is_tech_title(title, site):
            continue
        jid = stable_job_id(site, link)
        posted = j.get("posted_at") or j.get("posted_iso")
        if isinstance(posted, (int, float)):
            posted = datetime.fromtimestamp(posted, timezone.utc).isoformat()
        elif posted and len(str(posted)) == 10:
            posted = str(posted) + "T00:00:00+00:00"
        cur = conn.execute("INSERT OR IGNORE INTO jobs (id, portal, url, title, source, status, claimed_by, result, prio, posted_at, fetched_at) VALUES (?,?,?,?,?, 'pending', NULL, NULL, ?, ?, ?)",
                           (jid, portal, link, title, site, prio, posted, fetched_at))
        if cur.rowcount:
            existing.add(c)
            added += 1

conn.commit()
if wf_seen:
    seen = sqlite3.connect(WELLFOUND_SEEN_DB)
    seen.execute("CREATE TABLE IF NOT EXISTS seen (url TEXT PRIMARY KEY, injected_at INT)")
    seen.executemany("INSERT OR IGNORE INTO seen(url,injected_at) VALUES (?,strftime('%s','now'))", [(u,) for u in wf_seen])
    seen.commit()
    seen.close()
print(f"added {added} jobs from {os.path.basename(SRC)}")
for r in conn.execute("SELECT portal, status, COUNT(*) FROM jobs GROUP BY portal, status ORDER BY portal"):
    print(" ", r)
conn.close()
