import json, os, sqlite3, re, sys

from title_filter import is_tech_title

HERE = "/home/ubuntu/job_hunt_linkedin"
DB = os.path.join(HERE, "apply_queue.db")

def canonical(url):
    if not url: return ""
    u = url.strip()
    u = re.sub(r"(\?|&)(trackingId|refId|trk|utm_[a-z]+|from|position|pageNum|gclid|fbclid|ref)[^&]*", "", u)
    u = re.sub(r"[?&]+$", "", u)
    return u.rstrip("/")

conn = sqlite3.connect(DB)
existing = {canonical(r[0]) for r in conn.execute("SELECT url FROM jobs")}
applied = set()
if os.path.exists(os.path.join(HERE, "applications_log.tsv")):
    for line in open(os.path.join(HERE, "applications_log.tsv")):
        p = line.rstrip("\n").split("\t")
        if len(p) >= 5 and p[0] != "time":
            applied.add(p[4].strip())

SRC = sys.argv[1] if len(sys.argv) > 1 else "/tmp/site_collect.json"
data = json.load(open(SRC))
added = 0
PRIO = {"wellfound": 6, "wellfound_us": 6, "internshala": 5, "naukri": 4, "yc": 5, "weworkremotely": 1}
for s in data:
    site = s.get("site", "?")
    portal = "wellfound" if site.startswith("wellfound") else site
    prio = PRIO.get(site, 3)
    for j in s.get("jobs", []):
        link = j.get("link") or j.get("url") or ""
        c = canonical(link)
        if not c or c in existing:
            continue
        if c in applied or c.rstrip("/") in {a.rstrip("/") for a in applied}:
            continue
        title = (j.get("title") or "")[:120]
        if not is_tech_title(title, site):
            continue
        jid = f"{site}-{abs(hash(c))}"
        conn.execute("INSERT OR IGNORE INTO jobs (id, portal, url, title, source, status, claimed_by, result, prio) VALUES (?,?,?,?,?, 'pending', NULL, NULL, ?)",
                     (jid, portal, link, title, site, prio))
        existing.add(c)
        added += 1

conn.commit()
print(f"added {added} jobs from {os.path.basename(SRC)}")
for r in conn.execute("SELECT portal, status, COUNT(*) FROM jobs GROUP BY portal, status ORDER BY portal"):
    print(" ", r)
conn.close()
