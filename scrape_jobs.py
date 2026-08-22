#!/usr/bin/env python3
"""LinkedIn guest job scraper — resume-matched keywords, location=India/Kolkata, remote preferred.
Variants per keyword: India | Kolkata | India+remote(f_WT=2) | remote global(f_WT=2)
"""
import json, os
import os,random,re,sys,time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
import requests
from bs4 import BeautifulSoup

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

# f_TPR values: r3600=past hour, r86400=past 24h, r604800=past week, r2592000=past month
TPR = os.environ.get("LI_TPR", "r86400")
BUDGET = float(os.environ.get("LI_BUDGET", "0"))  # seconds; 0 = unlimited (clean exit 0 when hit)
_T0 = time.time()
PAUSE_PAGE_MIN = float(os.environ.get("LI_PAUSE_PAGE_MIN", "6"))
PAUSE_PAGE_MAX = float(os.environ.get("LI_PAUSE_PAGE_MAX", "9"))
PAUSE_KW_MIN = float(os.environ.get("LI_PAUSE_KW_MIN", "3"))
PAUSE_KW_MAX = float(os.environ.get("LI_PAUSE_KW_MAX", "5"))
VMAX_CAP = int(os.environ.get("LI_VMAX", "0"))  # 0 = unlimited; cap pages per variant

KEYWORD_GROUPS = {
    "automation": [
        "workflow automation", "n8n", "monday.com", "Make.com",
        "business process automation", "RPA developer", "AI automation engineer",
        "IT automation engineer", "test automation engineer", "QA automation engineer",
        "Zapier developer", "AI agent developer",
    ],
    "fullstack": [
        "full stack developer", "full stack engineer", "full-stack developer",
        "TypeScript developer", "Next.js developer", "React developer",
        "Node.js developer", "backend developer", "frontend developer",
    ],
    "web3": [
        "Solana developer", "Solana", "Web3 developer", "blockchain developer",
        "Rust developer", "smart contract developer", "Solidity developer",
        "crypto developer", "DeFi developer", "Web3 engineer",
    ],
    "ai_ml": [
        "AI engineer", "machine learning engineer", "LLM engineer",
        "Python developer", "AI product engineer", "RAG engineer", "AI full stack",
    ],
    "devops": [
        "DevOps engineer", "platform engineer", "SRE", "Docker Kubernetes engineer",
        "Cloud engineer",
    ],
    "itops": [
        "IT support engineer", "systems administrator", "Microsoft 365 administrator",
        "endpoint security analyst", "IT operations", "helpdesk engineer",
        "IT automation",
    ],
    "entry": [
        "fresher software engineer", "entry level software engineer", "entry level developer",
        "graduate trainee", "junior software engineer", "junior full stack developer",
        "associate software engineer", "software engineer 1", "software developer fresher",
        "1-3 years experience developer", "trainee software engineer", "software engineer intern",
        "junior react developer", "junior backend developer", "0-2 years experience",
    ],
}

MAX_PAGES_PER_KEYWORD = 6
PAGE_SIZE = 25
RATE_LIMIT_CODES = {429, 403, 999}

INDUSTRIAL_NOISE = re.compile(
    r"controls|manufactur|mfg |industrial|plant|production|cnc|plc|scada|hvac|"
    r"maintenance (tech|engineer)|electrical|mechanical|welding|assembly line|"
    r"fabricat|warehouse|logistics|forklift|tooling|fixture|mold|injection", re.I
)

# search variants: (name, extra params, max pages)
VARIANTS = [
    ("india", {"location": "India"}, 4),
    ("kolkata", {"location": "Kolkata"}, 3),
    ("india_remote", {"location": "India", "f_WT": "2"}, 4),
    ("remote", {"f_WT": "2"}, 6),
]


def fetch(keyword: str, start: int, extra: dict, session: requests.Session):
    params = {"keywords": keyword, "f_TPR": TPR, "start": start, **extra}
    url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    for attempt in range(4):
        try:
            r = session.get(url, params=params, timeout=25)
            if r.status_code in RATE_LIMIT_CODES:
                wait = 45 + attempt * 45
                print(f"  [429/403] {keyword} {extra} start={start} — sleep {wait}s", flush=True)
                time.sleep(wait)
                continue
            if r.status_code != 200:
                print(f"  [HTTP {r.status_code}] {keyword} start={start}", flush=True)
                return ""
            return r.text
        except requests.RequestException as e:
            print(f"  [err] {keyword} start={start}: {e}", flush=True)
            time.sleep(10)
    return ""


def parse(html: str):
    jobs = []
    if not html:
        return jobs
    soup = BeautifulSoup(html, "html.parser")
    for card in soup.select("div.job-search-card, li"):
        urn = card.get("data-entity-urn", "")
        m = re.search(r"(\d+)$", urn)
        jid = m.group(1) if m else None
        a = card.select_one("a.base-card__full-link")
        link = a.get("href") if a else None
        if link:
            link = re.split(r"[?&]", link)[0]
            mm = re.search(r"-(\d+)$", link)
            if not jid and mm:
                jid = mm.group(1)
        title_el = card.select_one("h3.base-search-card__title")
        title = title_el.get_text(strip=True) if title_el else ""
        comp_el = card.select_one("h4.base-search-card__subtitle")
        company = comp_el.get_text(strip=True) if comp_el else ""
        loc_el = card.select_one("span.job-search-card__location")
        location = loc_el.get_text(strip=True) if loc_el else ""
        date_el = card.select_one("time")
        date = date_el.get("datetime", "") if date_el else ""
        if title and jid:
            jobs.append({
                "id": jid, "title": title, "company": company,
                "location": location, "date": date, "link": link or "",
            })
    return jobs


def save_checkpoint(all_jobs, stats, done):
    jobs = list(all_jobs.values())
    raw_path = os.path.join(OUT_DIR, f"jobs_raw_{TPR}_india.json")
    with open(raw_path, "w") as f:
        total_keywords = sum(len(v) for v in KEYWORD_GROUPS.values())
        json.dump({"tpr": TPR, "count": len(jobs), "stats": stats,
                   "done": sorted(done), "complete": len(done) >= total_keywords,
                   "saved_at": int(time.time()), "jobs": jobs}, f, indent=1)


def main():
    # resume support
    raw_path = os.path.join(OUT_DIR, f"jobs_raw_{TPR}_india.json")
    all_jobs, stats, done = {}, {}, set()
    if os.path.exists(raw_path):
        try:
            d = json.load(open(raw_path))
            all_jobs = {j["id"]: j for j in d.get("jobs", [])}
            stats = d.get("stats", {})
            done = set(d.get("done", []))
            print(f"[resume] loaded {len(all_jobs)} jobs, {len(done)} keywords done", flush=True)
        except Exception as e:
            print(f"[resume] failed: {e} — starting fresh", flush=True)
            all_jobs, stats, done = {}, {}, set()

    # A completed checkpoint is the end of one search cycle, not a permanent
    # terminal state. Keep one prior snapshot for diagnosis and begin a clean
    # cycle so the next cron run performs real network collection.
    all_keywords = {kw for keywords in KEYWORD_GROUPS.values() for kw in keywords}
    if all_keywords and all_keywords.issubset(done):
        previous = raw_path + ".previous"
        try:
            os.replace(raw_path, previous)
        except OSError:
            pass
        all_jobs, stats, done = {}, {}, set()
        print(f"[cycle] prior checkpoint complete ({len(all_keywords)} keywords); starting fresh", flush=True)

    session = requests.Session()
    session.headers.update({"Accept-Language": "en-US,en;q=0.9"})

    for group, keywords in KEYWORD_GROUPS.items():
        for kw in keywords:
            if BUDGET and time.time() - _T0 > BUDGET:
                save_checkpoint(all_jobs, stats, done)
                print(f"\n[budget] {BUDGET:.0f}s elapsed — checkpoint saved, resuming next run", flush=True)
                return
            if kw in done:
                print(f"[skip] '{kw}' already done", flush=True)
                continue
            for vname, vextra, vmax in VARIANTS:
                if VMAX_CAP:
                    vmax = min(vmax, VMAX_CAP)
                session.headers["User-Agent"] = random.choice(UA_LIST)
                count = 0
                for page in range(vmax):
                    html = fetch(kw, page * PAGE_SIZE, vextra, session)
                    jobs = parse(html)
                    if not jobs:
                        break
                    fresh = 0
                    for j in jobs:
                        if INDUSTRIAL_NOISE.search(j["title"]):
                            continue
                        if j["id"] not in all_jobs:
                            j["found_via"] = [kw]
                            j["scopes"] = [vname]
                            all_jobs[j["id"]] = j
                            fresh += 1
                        else:
                            if kw not in all_jobs[j["id"]]["found_via"]:
                                all_jobs[j["id"]]["found_via"].append(kw)
                            if vname not in all_jobs[j["id"]]["scopes"]:
                                all_jobs[j["id"]]["scopes"].append(vname)
                    count += len(jobs)
                    print(f"[{group}] '{kw}' [{vname}] p{page+1}: {len(jobs)} cards ({fresh} new)", flush=True)
                    time.sleep(random.uniform(PAUSE_PAGE_MIN, PAUSE_PAGE_MAX))
                stats.setdefault(kw, {})[vname] = count
                time.sleep(random.uniform(PAUSE_KW_MIN, PAUSE_KW_MAX))
            done.add(kw)
            save_checkpoint(all_jobs, stats, done)
            print(f"[checkpoint] '{kw}' done — {len(all_jobs)} total jobs", flush=True)

    jobs = list(all_jobs.values())
    for j in jobs:
        j["group"] = next(
            (g for g, kws in KEYWORD_GROUPS.items() if j["found_via"][0] in kws), "other"
        )
        scopes = j["scopes"]
        loc = j["location"].lower()
        if "remote" in scopes or "india_remote" in scopes or "remote" in loc:
            j["worktype"] = "Remote"
        elif "hybrid" in loc:
            j["worktype"] = "Hybrid"
        else:
            j["worktype"] = "On-site"
        # preferred flags for sorting
        j["is_remote"] = j["worktype"] == "Remote"
        j["is_kolkata"] = "kolkata" in scopes or "kolkata" in loc
    jobs.sort(key=lambda x: (not x["is_remote"], not x["is_kolkata"]))

    raw_path = os.path.join(OUT_DIR, f"jobs_raw_{TPR}_india.json")
    save_checkpoint(all_jobs, stats, done)
    from collections import Counter
    wt = Counter(j["worktype"] for j in jobs)
    print(f"\nTOTAL UNIQUE: {len(jobs)}  ->  {raw_path}")
    print("worktype:", dict(wt))
    print("kolkata jobs:", sum(1 for j in jobs if j["is_kolkata"]))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # crash-safe: dump whatever was collected before failing
        import traceback
        traceback.print_exc()
        try:
            import json as _j, glob, os as _o
            raw_path = _o.path.join(OUT_DIR, f"jobs_raw_{TPR}_india.json")
            if _o.path.exists(raw_path):
                d = _j.load(open(raw_path))
                d["partial"] = True
                _j.dump(d, open(raw_path, "w"), indent=1)
        except Exception:
            pass
        sys.exit(1)
