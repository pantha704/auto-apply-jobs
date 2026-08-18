#!/usr/bin/env python3
"""1-YOE compatible shortlist — aggressive filter, positioning user at 1 year experience."""
import csv, json, re, os
from collections import Counter

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(OUT_DIR, "jobs_raw_r86400_india.json")))
jobs = d["jobs"]

# EXCLUDE only clearly out-of-reach: 5+ yrs explicit, senior/lead/principal/staff/head/architect/vp/director/chief
OUT = re.compile(
    r"\b(senior|sr\.?|lead|principal|staff|head|architect|vp|vice president|director|chief|"
    r"principal engineer|manager|5\+|5-|6\+|6-|7\+|8\+|9\+|10\+|10-|expert)\b", re.I)
# strong include signals: entry-level language + 1-3 yrs + intern/trainee/fresher
ENTRY_EXPLICIT = re.compile(
    r"\b(junior|entry|fresher|graduate|trainee|intern|apprentice|associate|early[- ]career|"
    r"new grad|0-?[12]|1\+|1-|1 to 2|1-2|2\+|2-|0-2|1-3|i\s*[-\u2013]\s*\d)\b", re.I)
FIT = re.compile(
    r"solana|web3|blockchain|crypto|rust|solidity|smart contract|defi|anchor|"
    r"full[ -]?stack|typescript|next\.?js|react|node\.?js|python|llm|"
    r"ai (engineer|developer|agent|automation|developer)|machine learning|"
    r"automation (engineer|developer)|n8n|workflow|integration (engineer|developer)|"
    r"api (engineer|developer)|devops|cloud (engineer|developer)|backend|frontend|"
    r"software (engineer|developer)|web developer|data engineer", re.I)

def bucket(t):
    t = t.lower()
    if re.search(r"solana|web3|blockchain|crypto|rust|solidity|defi|anchor", t): return "web3/solana/rust"
    if re.search(r"ai|llm|machine learning|genai|data engineer", t): return "ai/llm"
    if re.search(r"full[ -]?stack|frontend|backend|typescript|next|react|node|web developer", t): return "full-stack"
    if re.search(r"automation|workflow|n8n|integration|api", t): return "automation"
    if re.search(r"devops|cloud|platform|sre|kubernetes|docker", t): return "devops/cloud"
    if re.search(r"it |support|admin|security|helpdesk|m365|microsoft", t): return "it-ops"
    return "software dev (gen)"

sel = []
for j in jobs:
    t = j["title"]
    if OUT.search(t):
        continue
    if not FIT.search(t):
        continue
    j["bucket"] = bucket(t)
    j["entry_explicit"] = (j.get("group") == "entry") or bool(ENTRY_EXPLICIT.search(t))
    sel.append(j)

RANK = {"web3/solana/rust": 0, "ai/llm": 1, "full-stack": 2, "automation": 3,
        "devops/cloud": 4, "software dev (gen)": 5, "it-ops": 6}
sel.sort(key=lambda j: (not j["entry_explicit"], RANK[j["bucket"]],
                        not j.get("is_kolkata", False), j.get("date", "")))

lines = []
lines.append("# ✅ 1-YOE Compatible Shortlist — Pratham Jaiswal")
lines.append("")
lines.append("> **Positioning:** 1 year experience. Filter keeps every non-senior title carrying your stack — **entry/junior/fresher/associate/1-3yr jobs ranked first**, then mid-tier with strong stack match. Excludes only senior/lead/principal/staff/5+ yrs.")
lines.append(">")
lines.append(f"> **{len(sel)} compatible roles** from {len(jobs)}-job 24h pool (India/Kolkata/Remote)")
lines.append(">")
lines.append("> ⚠️ Title-level filter only — verify YOE + pay in full JD on LinkedIn before applying. Aggressive by design: mid titles often accept 1-2 yrs when portfolio is strong.")
lines.append("")
bc = Counter(j["bucket"] for j in sel)
ec = sum(1 for j in sel if j["entry_explicit"])
kc = sum(1 for j in sel if j.get("is_kolkata"))
lines.append(f"**Entry-explicit:** {ec} · **Kolkata-area:** {kc} · **Remote:** {sum(1 for j in sel if j.get('worktype')=='Remote')}")
lines.append("")
lines.append("### By stack")
lines.append("")
for k in ["web3/solana/rust", "ai/llm", "full-stack", "automation", "devops/cloud", "software dev (gen)", "it-ops"]:
    lines.append(f"- **{k}:** {bc.get(k, 0)}")
lines.append("")

last = None
n = 0
for j in sel:
    key = ("entry" if j["entry_explicit"] else "mid") + "|" + j["bucket"]
    if key != last:
        tag = "🌱 ENTRY-FRIENDLY" if j["entry_explicit"] else "💪 MID (1-2yr achievable)"
        lines.append(f"---\n## {j['bucket']} — {tag}\n")
        lines.append("| # | Job | Company | Location | Type | Posted | Link |")
        lines.append("|---|-----|---------|----------|------|--------|------|")
        last = key
    n += 1
    lines.append(f"| {n} | {j['title'].replace('|','\\\\|')} | {j['company'].replace('|','\\\\|')} | {j['location'].replace('|','\\\\|')} | {j.get('worktype','')} | {j['date']} | [view]({j['link']}) |")

open(os.path.join(OUT_DIR, "compatible_1yoe.md"), "w").write("\n".join(lines))
with open(os.path.join(OUT_DIR, "compatible_1yoe.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["id", "title", "company", "location", "worktype", "posted", "link", "bucket", "entry_explicit", "kolkata"])
    for j in sel:
        w.writerow([j["id"], j["title"], j["company"], j["location"], j.get("worktype", ""),
                    j["date"], j["link"], j["bucket"], "Y" if j["entry_explicit"] else "",
                    "Y" if j.get("is_kolkata") else ""])
print(f"1-YOE compatible: {len(sel)} (entry-explicit {ec}) -> compatible_1yoe.md/.csv")
