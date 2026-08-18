#!/usr/bin/env python3
"""Realtime fresh-job puller. Scrapes newest jobs (default 6h window), keeps seen-IDs,
writes ONLY new jobs to /tmp/realtime_new.json for immediate probing/applying.
Usage: python3 realtime_pull.py [f_TPR]  (default r21600 = 6h)
"""
import json, os
import os,random,re,sys,time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scrape_jobs import KEYWORD_GROUPS, UA_LIST, INDUSTRIAL_NOISE, parse

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
TPR = sys.argv[1] if len(sys.argv) > 1 else "r21600"
SEEN = os.path.join(OUT_DIR, "seen_ids.json")
NEW = "/tmp/realtime_new.json"
PAGES = 2          # fast pull: 2 pages per variant
VARIANTS = [("india", {"location": "India"}), ("kolkata", {"location": "Kolkata"}),
            ("india_remote", {"location": "India", "f_WT": "2"}), ("remote", {"f_WT": "2"})]

seen = set()
if os.path.exists(SEEN):
    seen = set(json.load(open(SEEN)))

s = requests.Session()
s.headers.update({"Accept-Language": "en-US,en;q=0.9"})
new_jobs, stats = {}, {}

for group, keywords in KEYWORD_GROUPS.items():
    for kw in keywords:
        s.headers["User-Agent"] = random.choice(UA_LIST)
        count = 0
        for vname, vextra in VARIANTS:
            for page in range(PAGES):
                try:
                    r = s.get("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
                              params={"keywords": kw, "f_TPR": TPR, "start": page * 25, **vextra}, timeout=20)
                    if r.status_code != 200:
                        time.sleep(30); continue
                    jobs = parse(r.text)
                except Exception:
                    time.sleep(15); continue
                if not jobs:
                    break
                for j in jobs:
                    if INDUSTRIAL_NOISE.search(j["title"]):
                        continue
                    if j["id"] not in seen and j["id"] not in new_jobs:
                        j["found_via"] = [kw]
                        j["group"] = group
                        j["scope"] = vname
                        new_jobs[j["id"]] = j
                count += len(jobs)
                time.sleep(random.uniform(0.5, 1.0))
        time.sleep(random.uniform(0.6, 1.2))

# persist seen (union old + new)
seen |= set(new_jobs.keys())
json.dump(sorted(seen), open(SEEN, "w"))
json.dump({"tpr": TPR, "count": len(new_jobs), "jobs": list(new_jobs.values())},
          open(NEW, "w"), indent=1)
print(f"realtime: {len(new_jobs)} NEW jobs (window {TPR}) -> {NEW}", flush=True)
