#!/usr/bin/env bash
# Job hunt 6h report — writes markdown doc, prints path + summary for delivery
set -u
cd /home/ubuntu/job_hunt_linkedin || exit 1
PY=/home/ubuntu/jobhunt-venv/bin/python
OUTDIR=/home/ubuntu/job_hunt_reports
mkdir -p "$OUTDIR"
TS=$(date '+%Y%m%d_%H%M')
OUT="$OUTDIR/jobhunt_report_$TS.md"

# Collect stats
STATS=$($PY - <<'PYEOF'
import sqlite3, json, os
c = sqlite3.connect("apply_queue.db")
rows = list(c.execute("SELECT portal, status, COUNT(*) FROM jobs GROUP BY portal, status ORDER BY portal"))
tot = sum(n for _,_,n in rows); pend = sum(n for p,s,n in rows if s=='pending')
done = sum(n for p,s,n in rows if s=='done'); skip = sum(n for p,s,n in rows if s=='skip')
print(f"TOTAL={tot} PENDING={pend} DONE={done} SKIP={skip}")
for p,s,n in rows: print(f"{p}|{s}|{n}")
print("RECENT")
# REAL truth: audit log — submitted applications by applied_at, not jobs.rowid
for r in c.execute("SELECT company, role, portal, applied_at FROM applications WHERE status='submitted' ORDER BY applied_at DESC LIMIT 8"):
    print(f"{r[3]}|{r[2]}|{str(r[0])[:40]}|{str(r[1])[:40]}")
PYEOF
)

# Latest application activity: DB truth (TSV is a stale pre-systemd artifact)
APP_TAIL=$($PY - <<'PYEOF'
import sqlite3
c = sqlite3.connect("apply_queue.db")
seen = set()
for r in c.execute("SELECT applied_at, portal, company, role, status FROM applications ORDER BY applied_at DESC LIMIT 30"):
    key = (r[3], r[4])
    if key in seen: continue
    seen.add(key)
    print(f"{r[0]}\t{r[1]}\t{r[2]}\t{r[3]}\t{r[4]}")
    if len(seen) >= 8: break
PYEOF
)

# Worker health — ALL portals, not just linkedin/wellfound
WORKERS=$(ps aux | grep -E "[w]orker_(linkedin|wellfound|internshala|external|review|yc)" | awk '{print $2, $3"%", $12, $13}' | tr '\n' ';')

# Session health
SESSIONS=$($PY - <<'PYEOF'
import json, os
out = []
try:
    li = json.load(open("li_state.json"))
    out.append(f"linkedin li_at={'YES' if any(c.get('name')=='li_at' for c in li.get('cookies',[])) else 'NO'}")
except Exception as e: out.append(f"linkedin ERR {e}")
try:
    wf = json.load(open("portal_wellfound.json"))
    out.append(f"wellfound {len(wf.get('cookies',[]))} cookies")
except Exception as e: out.append(f"wellfound ERR {e}")
print("; ".join(out))
PYEOF
)

# Queue freshness — newest of the three scrape checkpoints
FRESH=$($PY - <<'PYEOF'
import os, time
picks = ["jobs_raw_r3600_india.json", "jobs_raw_r86400_india.json", "/tmp/site_collect.json"]
ages = [(os.path.getmtime(p), os.path.basename(p)) for p in picks if os.path.exists(p)]
if ages:
    m, name = max(ages)
    age = (time.time() - m) / 3600
    print(f"{age:.1f}h ({name})")
else:
    print("missing")
PYEOF
)

# Compose doc
{
echo "# 🤖 Job Hunt — 6h Progress Report"
echo ""
echo "**$(date '+%Y-%m-%d %H:%M %Z')**"
echo ""
echo "## 📊 Queue"
echo ""
echo "| Metric | Count |"
echo "|---|---|"
echo "$STATS" | grep -E "^TOTAL" | sed 's/TOTAL=\(.*\) PENDING=\(.*\) DONE=\(.*\) SKIP=\(.*\)/| Total jobs | \1 |\n| Pending | \2 |\n| Applied (done) | \3 |\n| Skipped | \4 |/'
echo ""
echo "### By portal/status"
echo ""
echo "| Portal | Status | Count |"
echo "|---|---|---|"
echo "$STATS" | grep -E "^[a-z]" | sed 's/^\([a-z]*\)|\([a-z_]*\)|\([0-9]*\)$/| \1 | \2 | \3 |/'
echo ""
echo "## ✅ Recent submissions (audit log, newest first)"
echo ""
echo "| Time | Portal | Company | Role |"
echo "|---|---|---|---|"
echo "$STATS" | sed -n '/^RECENT/,$p' | tail -n +2 | sed 's/^\([^|]*\)|\([^|]*\)|\([^|]*\)|\([^|]*\)$/| \1 | \2 | \3 | \4 |/'
echo ""
echo "## 🖥️ Workers"
echo ""
if [ -n "$WORKERS" ]; then
  echo "$WORKERS" | tr ';' '\n' | sed 's/^/- /'
else
  echo "- ⚠️ NO WORKERS RUNNING"
fi
echo ""
echo "## 🔑 Sessions"
echo ""
echo "$SESSIONS" | tr ';' '\n' | sed 's/^/- /'
echo ""
echo "## 🕐 Queue freshness (last scrape)"
echo ""
echo "- $FRESH"
echo ""
echo "## 📋 Latest application activity (DB)"
echo ""
echo '```'
echo "$APP_TAIL" | sed 's/\t/  /g'
echo '```'
echo ""
echo "---"
echo "*Generated automatically every 6h by the job-hunt pipeline on Panther VPS*"
} > "$OUT"

echo "DOC:$OUT"
Q=$(echo "$STATS" | grep -E "^TOTAL" | sed 's/TOTAL=\([0-9]*\).*PENDING=\([0-9]*\).*DONE=\([0-9]*\).*/Q=\1 P=\2 D=\3/')
echo "SUMMARY: $Q Workers: ${WORKERS:-none}"