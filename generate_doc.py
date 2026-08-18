#!/usr/bin/env python3
"""Build India/Kolkata/remote job opportunities doc from scraped raw JSON."""
import csv, json, os

OUT_DIR = os.path.dirname(os.path.abspath(__file__))
GROUP_LABELS = {
    "automation": "🤖 Automation / Workflow Engineering",
    "fullstack": "🧱 Full-Stack / Web Development",
    "web3": "⛓️ Web3 / Solana / Blockchain",
    "ai_ml": "🧠 AI / ML / Data Engineering",
    "devops": "⚙️ DevOps / Platform / SRE",
    "itops": "🛠️ IT Operations / Systems / Security",
    "entry": "🌱 Entry-Level / Fresher / Junior",
    "other": "📦 Other",
}
GROUPS = ["automation", "fullstack", "web3", "ai_ml", "devops", "itops", "entry"]
WT_RANK = {"Remote": 0, "Hybrid": 1, "On-site": 2}

with open(os.path.join(OUT_DIR, "jobs_raw_r86400_india.json")) as f:
    data = json.load(f)

jobs = data["jobs"]
tpr = data["tpr"]

groups = {}
for j in jobs:
    groups.setdefault(j.get("group", "other"), []).append(j)

def sortkey(j):
    return (WT_RANK.get(j.get("worktype", "On-site"), 2),
            not j.get("is_kolkata", False),
            j.get("date", ""))

for g in groups:
    groups[g].sort(key=sortkey)

lines = []
lines.append("# 🎯 LinkedIn Job Opportunities — Pratham Jaiswal (India / Kolkata / Remote)")
lines.append("")
lines.append(f"> **Generated:** 2026-08-12 · **Source:** LinkedIn guest job search · **Time filter:** Past 24 hours (`f_TPR={tpr}`)")
lines.append(">")
lines.append("> **Location scope:** India · Kolkata · Remote (global, `f_WT=2`) · India Remote")
lines.append(">")
lines.append("> **Total unique opportunities:** {:,}".format(len(jobs)))
n_remote = sum(1 for j in jobs if j.get("worktype") == "Remote")
n_hybrid = sum(1 for j in jobs if j.get("worktype") == "Hybrid")
n_kol = sum(1 for j in jobs if j.get("is_kolkata"))
lines.append(f"> **Remote:** {n_remote} · **Hybrid:** {n_hybrid} · **Kolkata-area:** {n_kol}")
lines.append(">")
lines.append("> Matched against resume profile: **Automation Engineer | Full-Stack Developer** — TypeScript, Python, Rust, Node.js, Next.js, React, n8n, monday.com, QStash, Solana/Anchor, AI/ML (Groq/NVIDIA NIM/RAG), PostgreSQL/Prisma/Redis, Docker/K8s, M365/endpoint security, 4 merged OSS PRs (Rust, PyTorch, DeepMind, CircuitVerse).")
lines.append(">")
lines.append("> Rows sorted remote-first → hybrid → on-site, Kolkata jobs pinned high within each tier. Categories = resume skill area whose search keyword surfaced the job.")
lines.append("")
lines.append("---")
lines.append("")
lines.append("## 📊 Summary by Category")
lines.append("")
lines.append("| Category | Count | Remote | Kolkata |")
lines.append("|----------|------:|-------:|--------:|")
for g in GROUPS + ["other"]:
    gl = groups.get(g, [])
    lines.append(f"| {GROUP_LABELS[g]} | {len(gl)} | {sum(1 for j in gl if j.get('worktype')=='Remote')} | {sum(1 for j in gl if j.get('is_kolkata'))} |")
lines.append(f"| **Total** | **{len(jobs)}** | **{n_remote}** | **{n_kol}** |")
lines.append("")

for g in GROUPS + ["other"]:
    glist = groups.get(g, [])
    if not glist:
        continue
    via = sorted({kw for j in glist for kw in j.get("found_via", [])})
    lines.append("---")
    lines.append("")
    lines.append(f"## {GROUP_LABELS[g]} — {len(glist)} jobs")
    lines.append("")
    lines.append(f"*Matched via keywords: {', '.join(via)}*")
    lines.append("")
    lines.append("| # | Job Title | Company | Location | Type | Posted | Link |")
    lines.append("|---|-----------|---------|----------|------|--------|------|")
    for i, j in enumerate(glist, 1):
        title = j["title"].replace("|", "\\|")
        comp = j["company"].replace("|", "\\|")
        loc = j["location"].replace("|", "\\|")
        wt = j.get("worktype", "On-site")
        lines.append(f"| {i} | {title} | {comp} | {loc} | {wt} | {j['date']} | [view]({j['link']}) |")
    lines.append("")

doc = "\n".join(lines)
doc_path = os.path.join(OUT_DIR, "linkedin_job_opportunities_india.md")
with open(doc_path, "w") as f:
    f.write(doc)

csv_path = os.path.join(OUT_DIR, "linkedin_job_opportunities_india.csv")
with open(csv_path, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["id", "title", "company", "location", "worktype", "kolkata", "posted", "link", "category", "matched_via"])
    for g in GROUPS + ["other"]:
        for j in groups.get(g, []):
            w.writerow([j["id"], j["title"], j["company"], j["location"], j.get("worktype", ""),
                        "Y" if j.get("is_kolkata") else "", j["date"], j["link"], g,
                        "; ".join(j.get("found_via", []))])

print(f"Wrote {doc_path} ({len(jobs)} jobs)")
print(f"Wrote {csv_path}")
print("Per group:", {g: len(groups.get(g, [])) for g in GROUPS + ['other']})
