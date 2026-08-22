#!/usr/bin/env bash
# Job hunt 6h report — writes markdown doc, prints path + summary for delivery
set -u
export TZ=Asia/Kolkata
export JOBHUNT_QUEUE_DB=/var/lib/jobhunt/apply_queue.db
cd /home/ubuntu/job_hunt_linkedin || exit 1
PY=/home/ubuntu/jobhunt-venv/bin/python
OUTDIR=/home/ubuntu/job_hunt_reports
mkdir -p "$OUTDIR"
TS=$(date '+%Y%m%d_%H%M')
OUT="$OUTDIR/jobhunt_report_$TS.md"

# Collect stats
STATS=$($PY - <<'PYEOF'
import sqlite3, json, os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
c = sqlite3.connect(os.environ["JOBHUNT_QUEUE_DB"])
rows = list(c.execute("SELECT portal, status, COUNT(*) FROM jobs GROUP BY portal, status ORDER BY portal"))
tot = sum(n for _,_,n in rows); pend = sum(n for p,s,n in rows if s=='pending')
processed = sum(n for p,s,n in rows if s=='done'); skip = sum(n for p,s,n in rows if s=='skip')
confirmed = c.execute("SELECT COUNT(*) FROM applications WHERE status IN ('submitted','applied')").fetchone()[0]
already = c.execute("SELECT COUNT(*) FROM applications WHERE status IN ('already-applied','already_applied')").fetchone()[0]
print(f"TOTAL={tot} PENDING={pend} PROCESSED={processed} SKIP={skip} CONFIRMED={confirmed} ALREADY={already}")
for p,s,n in rows: print(f"{p}|{s}|{n}")
print("RECENT")
# REAL truth: audit log — submitted applications by applied_at, not jobs.rowid
for r in c.execute("SELECT company, role, portal, applied_at FROM applications WHERE status IN ('submitted','applied') ORDER BY applied_at DESC LIMIT 8"):
    dt=datetime.fromisoformat(str(r[3]).replace('Z','+00:00'))
    if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
    when=dt.astimezone(ZoneInfo('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M IST')
    print(f"{when}|{r[2]}|{str(r[0])[:40]}|{str(r[1])[:40]}")
PYEOF
)

# Latest application activity: DB truth (TSV is a stale pre-systemd artifact)
APP_TAIL=$($PY - <<'PYEOF'
import sqlite3, os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
c = sqlite3.connect(os.environ["JOBHUNT_QUEUE_DB"])
seen = set()
for r in c.execute("SELECT applied_at, portal, company, role, status FROM applications ORDER BY applied_at DESC LIMIT 30"):
    key = (r[3], r[4])
    if key in seen: continue
    seen.add(key)
    dt=datetime.fromisoformat(str(r[0]).replace('Z','+00:00'))
    if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
    when=dt.astimezone(ZoneInfo('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M IST')
    print(f"{when}\t{r[1]}\t{r[2]}\t{r[3]}\t{r[4]}")
    if len(seen) >= 8: break
PYEOF
)

# Worker health — ALL portals, not just linkedin/wellfound
WORKERS=$(systemctl list-units --type=service --state=running 'jobhunt-*@*.service' --no-legend --plain 2>/dev/null | awk '{print $1}' | tr '\n' ';')

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

# Source freshness — actual posting dates when available, never newest-file-wins
FRESH=$($PY - <<'PYEOF'
import json, os, time
from datetime import datetime, timezone
sources=[("LinkedIn 1h","jobs_raw_r3600_india.json"),("LinkedIn 24h","jobs_raw_r86400_india.json"),("Wellfound","/tmp/wellfound_fresh.json"),("Sites","/tmp/site_collect.json")]
now=datetime.now(timezone.utc)
for label,p in sources:
    if not os.path.exists(p):
        print(f"- {label}: missing"); continue
    file_age=(time.time()-os.path.getmtime(p))/3600
    try:
        data=json.load(open(p)); jobs=data.get('jobs',[]) if isinstance(data,dict) else [j for g in data if isinstance(g,dict) for j in g.get('jobs',[])]
        stamps=[]; day_only=[]
        for j in jobs:
            raw=j.get('posted_at') or j.get('date') or j.get('posted_iso')
            if raw is None: continue
            try:
                text=str(raw)
                if not isinstance(raw,(int,float)) and len(text)==10 and text[4]=='-' and text[7]=='-':
                    day_only.append(datetime.fromisoformat(text).date()); continue
                dt=datetime.fromtimestamp(raw,timezone.utc) if isinstance(raw,(int,float)) else datetime.fromisoformat(text.replace('Z','+00:00'))
                if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
                stamps.append(dt)
            except Exception: pass
        if stamps:
            post=f", newest posting {(now-max(stamps)).total_seconds()/3600:.1f}h ago"
        elif day_only:
            days=(now.date()-max(day_only)).days
            post=", newest posting today (date-only precision)" if days==0 else f", newest posting {days}d ago (date-only precision)"
        else:
            post=", posting age unavailable"
        print(f"- {label}: file {file_age:.1f}h old{post}")
    except Exception as e:
        print(f"- {label}: invalid ({str(e)[:50]})")
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
echo "$STATS" | grep -E "^TOTAL" | sed 's/TOTAL=\([^ ]*\) PENDING=\([^ ]*\) PROCESSED=\([^ ]*\) SKIP=\([^ ]*\) CONFIRMED=\([^ ]*\) ALREADY=\([^ ]*\)/| Total queue rows | \1 |\n| Pending | \2 |\n| Processed routes | \3 |\n| Skipped | \4 |\n| Confirmed applications | \5 |\n| Already applied | \6 |/'
echo ""
echo "### By portal/status"
echo ""
echo "| Portal | Status | Count |"
echo "|---|---|---|"
echo "$STATS" | grep -E "^[a-z]" | sed 's/^\([a-z]*\)|\([a-z_]*\)|\([0-9]*\)$/| \1 | \2 | \3 |/'
echo ""
echo "## ✅ Recent submissions (audit log, newest first)"
echo ""
echo "> Confirmed-record history; older entries may predate the current hardened filters."
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
echo "## 🕐 Source freshness"
echo ""
echo "$FRESH"
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

echo "MEDIA:$OUT"
Q=$(echo "$STATS" | grep -E "^TOTAL" | sed 's/TOTAL=\([0-9]*\).*PENDING=\([0-9]*\).*CONFIRMED=\([0-9]*\).*/Q=\1 P=\2 A=\3/')
echo "SUMMARY: $Q Workers: ${WORKERS:-none}"