#!/usr/bin/env python3
"""Multi-source job puller: HN Who's Hiring (Algolia API) + browser-based site collectors.
Usage: python3 multisource_pull.py
Writes new jobs (deduped vs seen) to /tmp/multisource_new.json
"""
import json, os
import os,re,sys,time,html
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
import requests
from datetime import datetime, timezone

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
SEEN = os.path.join(OUT_DIR, "seen_multisource.json")
OUT = "/tmp/multisource_new.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"

KEYWORDS = re.compile(
    r"typescript|react|next\.?js|node\.?js|rust|solana|web3|blockchain|crypto|solidity|smart contract|"
    r"python|full-?stack|backend|frontend|ai|ml|machine learning|llm|agent|devops|sre|platform|"
    r"automation|workflow|n8n|postgres|prisma|docker|kubernetes|cloud|software engineer|developer|"
    r"intern|junior|fresher|graduate", re.I)

def load_seen():
    if os.path.exists(SEEN):
        return set(json.load(open(SEEN)))
    return set()

def save_seen(seen):
    json.dump(sorted(seen), open(SEEN, "w"))

def hn_pull(seen):
    jobs = []
    r = requests.get("https://hn.algolia.com/api/v1/search",
                     params={"tags": "story,author_whoishiring", "query": "hiring",
                             "hitsPerPage": 10, "numericFilters": "created_at_i>1780000000"},
                     timeout=30, headers={"User-Agent": UA})
    hits = r.json().get("hits", [])
    latest = max(hits, key=lambda h: h.get("created_at_i", 0))
    item_id = latest["objectID"]
    r = requests.get(f"https://hn.algolia.com/api/v1/items/{item_id}", timeout=60, headers={"User-Agent": UA})
    data = r.json()

    def walk(node, depth=0):
        text = html.unescape(re.sub(r"<[^>]+>", " ", node.get("text", "") or ""))
        text = re.sub(r"\s+", " ", text).strip()
        if depth == 1 and node.get("author") != "whoishiring" and text and KEYWORDS.search(text):
            first_line = text.split(".")[0][:90] if "." in text else text[:90]
            jobs.append({
                "source": "hn", "id": f"hn-{node.get('id')}", "title": first_line,
                "company": first_line.split("|")[0].strip()[:60],
                "location": "", "link": f"https://news.ycombinator.com/item?id={node.get('id')}",
                "text": text[:300], "date": datetime.fromtimestamp(node.get("created_at_i", 0), timezone.utc).strftime("%Y-%m-%d"),
            })
        for c in node.get("children", []):
            walk(c, depth + 1)

    walk(data)
    return jobs

def main():
    seen = load_seen()
    all_jobs = []
    print("HN pull...", flush=True)
    for j in hn_pull(seen):
        if j["id"] not in seen:
            all_jobs.append(j)
    new = [j for j in all_jobs if j["id"] not in seen]
    seen |= {j["id"] for j in all_jobs}
    save_seen(seen)
    json.dump({"count": len(new), "jobs": new}, open(OUT, "w"), indent=1)
    print(f"multisource: {len(new)} new (HN) -> {OUT}", flush=True)

if __name__ == "__main__":
    main()
