#!/usr/bin/env python3
"""Build unified apply queue (SQLite) from all sources, deduped by canonical URL."""
import json, os
import os, re, sqlite3, sys
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")

from job_identity import canonical_url as canonical, stable_job_id
from title_filter import is_tech_title

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apply_queue.db")


def main():
    if os.path.exists(DB):
        os.remove(DB)
    conn = sqlite3.connect(DB)
    conn.execute("""CREATE TABLE jobs (
        id TEXT PRIMARY KEY, portal TEXT, url TEXT, title TEXT,
        source TEXT, status TEXT DEFAULT 'pending', claimed_by TEXT, result TEXT)""")
    jobs = {}

    # exclude already-handled links
    applied = set()
    if os.path.exists("applications_log.tsv"):
        for line in open("applications_log.tsv"):
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 5 and parts[0] != "time":
                applied.add(canonical(parts[4]))

    def add(portal, url, title, source):
        c = canonical(url)
        if not c or c in jobs: return
        if not is_tech_title(title, source): return
        if c in applied or c.rstrip("/") in {a.rstrip("/") for a in applied}: return
        jid = stable_job_id(source, url)
        jobs[c] = (jid, portal, url, (title or "")[:120], source)

    # 1. LinkedIn targets (Kolkata/remote/India from full pool)
    li = json.load(open("jobs_raw_r86400_india.json"))
    SEN = re.compile(r"\b(senior|lead|principal|staff|manager|director|head|architect|vp|chief|sr\.?)\b", re.I)
    for j in li["jobs"]:
        if SEN.search(j.get("title","")): continue
        scopes = j.get("scopes", [])
        loc = (j.get("location") or "").lower()
        if "kolkata" in loc or "india_remote" in scopes or j.get("is_kolkata"):
            add("linkedin", j["link"], j["title"], "linkedin")

    # 2. Indeed
    if os.path.exists("/tmp/indeed_jobs.json"):
        ind = json.load(open("/tmp/indeed_jobs.json"))
        for j in ind["jobs"]:
            add("indeed", j["link"], j["title"], "indeed")

    # 3. Naukri
    sc = json.load(open("/tmp/site_collect.json"))
    for s in sc:
        if s.get("site") == "naukri":
            for j in s["jobs"]:
                add("naukri", j["link"], j["title"], "naukri")
        if s.get("site") == "wellfound":
            for j in s["jobs"]:
                add("wellfound", j["link"], j["title"], "wellfound")
        if s.get("site") == "internshala":
            for j in s["jobs"]:
                add("internshala", j["link"], j["title"], "internshala")

    # 4. HN apply links
    if os.path.exists("/tmp/hn_top.json"):
        for o in json.load(open("/tmp/hn_top.json")):
            for u in o["urls"]:
                if "linkedin.com/jobs" in u: add("linkedin", u, o["title"], "hn")
                elif "wellfound" in u: add("wellfound", u, o["title"], "hn")
                elif "naukri" in u: add("naukri", u, o["title"], "hn")
                elif "lever" in u or "greenhouse" in u or "workable" in u or "breezy" in u:
                    add("ats", u, o["title"], "hn")

    # 5. X apply links
    if os.path.exists("/tmp/x_jobs.json"):
        for t in json.load(open("/tmp/x_jobs.json"))["jobs"]:
            for u in t.get("apply_links", []):
                if "linkedin.com/jobs" in u: add("linkedin", u, t["text"][:80], "x")
                elif "wellfound" in u: add("wellfound", u, t["text"][:80], "x")
                elif "naukri" in u: add("naukri", u, t["text"][:80], "x")

    conn.executemany("INSERT OR IGNORE INTO jobs (id, portal, url, title, source) VALUES (?,?,?,?,?)",
                     list(jobs.values()))
    conn.commit()
    from collections import Counter
    cnt = Counter(p for _, p, _, _, _ in jobs.values())
    print(f"QUEUE: {len(jobs)} jobs")
    for p, n in cnt.most_common(): print(f"  {p}: {n}")
    conn.close()

if __name__ == "__main__":
    main()
