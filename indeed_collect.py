#!/usr/bin/env python3
"""Indeed collector via CloakBrowser. Keywords + fromage=1 (past 24h), India location."""
from playwright.sync_api import sync_playwright
import json, os
import re,sys,time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
OUT = "/tmp/indeed_jobs.json"
KEYWORDS = ["full stack developer", "software engineer", "react developer", "python developer",
            "web developer", "automation engineer", "devops engineer", "ai engineer",
            "machine learning", "rust developer", "web3", "blockchain", "frontend developer"]
LOC = "India"

def extract(page):
    return page.evaluate("""() => {
      const out = [];
      document.querySelectorAll('.jobsearch-ResultsList .result, .job_seen_beacon, [data-jk]').forEach(card => {
        const a = card.querySelector('h2 a, .jcs-JobTitle');
        const title = a ? (a.innerText || a.textContent).trim().slice(0,100) : '';
        if (!title) return;
        const jk = a ? (a.href || '').match(/jk=([0-9a-f]+)/) : null;
        const company = card.querySelector('[data-testid="company-name"], .companyName, .css-63koeb');
        const loc = card.querySelector('[data-testid="text-location"], .companyLocation, .css-1restlb');
        const date = card.querySelector('[data-testid="job-date"], .date');
        out.push({
          title,
          company: company ? company.innerText.trim().slice(0,60) : '',
          location: loc ? loc.innerText.trim().slice(0,50) : '',
          date: date ? date.innerText.trim().slice(0,30) : '',
          jk: jk ? jk[1] : '',
        });
      });
      return out;
    }""")

results = []
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir="/tmp/cloak_profile_indeed", executable_path=CLOAK, headless=True,
        args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
              "--window-size=1280,900"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    for kw in KEYWORDS:
        seen, jobs = set(), []
        for page_n in range(4):
            url = f"https://in.indeed.com/jobs?q={kw.replace(' ','+')}&l={LOC}&fromage=1&start={page_n*10}"
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(4000)
                cards = extract(page)
                for c in cards:
                    if c["jk"] and c["jk"] not in seen:
                        seen.add(c["jk"])
                        c["link"] = f"https://in.indeed.com/viewjob?jk={c['jk']}"
                        c["keyword"] = kw
                        jobs.append(c)
            except Exception as e:
                print(f"[{kw}] p{page_n} ERR {str(e)[:80]}", flush=True)
                break
            if not cards:
                break
            time.sleep(1.5)
        results.append({"keyword": kw, "count": len(jobs), "jobs": jobs})
        print(f"[{kw}] {len(jobs)} jobs", flush=True)
        time.sleep(2)
    ctx.close()

all_jobs = [j for r in results for j in r["jobs"]]
seen2 = set(); uniq = []
for j in all_jobs:
    if j["jk"] not in seen2:
        seen2.add(j["jk"]); uniq.append(j)
json.dump({"count": len(uniq), "by_keyword": {r["keyword"]: r["count"] for r in results}, "jobs": uniq},
          open(OUT, "w"), indent=1)
print(f"TOTAL UNIQUE: {len(uniq)} -> {OUT}")
