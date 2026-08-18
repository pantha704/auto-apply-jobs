#!/usr/bin/env python3
"""sanity_check.py — golden tests for the job-farm's pure logic + DB schema.

Runs fast, no network, no browser. Exit 0 = healthy, 1 = broken.
Wire into the watchdog cron for automatic regression detection:
    /home/ubuntu/jobhunt-venv/bin/python sanity_check.py || echo "SANITY FAIL"
"""
import hashlib, os, sqlite3, sys, re

HERE = os.path.dirname(os.path.abspath(__file__))
FAILS = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok  {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL {name} {detail}")


print("== title_filter ==")
from title_filter import is_tech_title
check("rejects fundraising", not is_tech_title("Fundraising Internship", "internshala"))
check("rejects marketing", not is_tech_title("Marketing", "internshala"))
check("rejects category page", not is_tech_title("Remote Sales Manager Jobs", "wellfound"))
check("accepts marketplace (no false positive)", is_tech_title("Senior Full Stack Engineer II, Marketplace", "wellfound"))
check("accepts sales engineer", is_tech_title("Sales Engineer", "wellfound"))
check("accepts dev roles", is_tech_title("Backend Developer", "linkedin"))

print("== jd_match ==")
import jd_match
res = jd_match.analyze("We need a senior Rust engineer with 10 years of systems programming, US citizens only.")
check("blocks citizens-only", res["decision"] == "skip", str(res))
res2 = jd_match.analyze("Looking for a React + TypeScript developer to build dashboards with Node and PostgreSQL.")
check("applies to stack match", res2["decision"] == "apply", str(res2))
check("generates honest note", len(res2.get("note", "")) > 20)

print("== audit.canonical ==")
import audit
check("strips tracking params", audit.canonical("https://x.com/jobs/1?trk=abc&utm_source=x&pageNum=2") == "https://x.com/jobs/1")
check("normalizes trailing slash", audit.canonical("https://x.com/jobs/1/") == "https://x.com/jobs/1")
check("empty url safe", audit.canonical("") == "")

print("== DB schema ==")
try:
    c = sqlite3.connect(os.path.join(HERE, "apply_queue.db"))
    cols = [r[1] for r in c.execute("PRAGMA table_info(jobs)")]
    for need in ("id", "portal", "url", "title", "status", "result", "prio"):
        check(f"jobs.{need}", need in cols)
    acols = [r[1] for r in c.execute("PRAGMA table_info(applications)")]
    for need in ("portal", "company", "role", "url", "status", "url_hash"):
        check(f"applications.{need}", need in acols)
    n = c.execute("SELECT COUNT(*) FROM applications WHERE status='submitted'").fetchone()[0]
    check("submissions recorded", n > 0, f"n={n}")
    c.close()
except Exception as e:
    check("db open", False, str(e))

print("== worker code imports ==")
for mod in ("worker_wellfound", "worker_yc", "worker_review", "worker_external", "worker_internshala", "watchdog"):
    try:
        __import__(mod)
        check(f"import {mod}", True)
    except Exception as e:
        check(f"import {mod}", False, str(e)[:80])

print()
if FAILS:
    print(f"SANITY FAIL: {len(FAILS)} broken: {FAILS}")
    sys.exit(1)
print("SANITY OK")
