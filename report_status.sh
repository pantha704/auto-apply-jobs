#!/usr/bin/env bash
# Job hunt status collector — prints stats for the 6h report (run on VPS)
set -u
cd /home/ubuntu/job_hunt_linkedin || exit 1
PY=/home/ubuntu/jobhunt-venv/bin/python
NOW=$(date '+%Y-%m-%d %H:%M %Z')

echo "REPORT_TIME: $NOW"
echo "=== QUEUE ==="
$PY - <<'PYEOF'
import sqlite3
c = sqlite3.connect("apply_queue.db")
rows = list(c.execute("SELECT portal, status, COUNT(*) FROM jobs GROUP BY portal, status ORDER BY portal"))
tot = sum(n for _,_,n in rows)
pend = sum(n for p,s,n in rows if s=='pending')
done = sum(n for p,s,n in rows if s=='done')
skip = sum(n for p,s,n in rows if s=='skip')
print(f"TOTAL={tot} PENDING={pend} DONE={done} SKIP={skip}")
for p,s,n in rows:
    print(f"  {p:10s} {s:8s} {n}")
# recent done (last 10)
print("=== RECENT SUBMISSIONS ===")
try:
    for r in c.execute("SELECT url, title, status, claimed_by FROM jobs WHERE status='done' ORDER BY rowid DESC LIMIT 10"):
        print(f"  {r[3] or '?':8s} | {r[2]:8s} | {r[1][:60]} | {r[0][:70]}")
except Exception as e:
    print("  err", e)
PYEOF
echo "=== WORKERS ==="
ps aux | grep -E "[w]orker_(linkedin|wellfound)" | awk '{print $2, $3"%", $12, $13}' || echo "  NO WORKERS RUNNING"
echo "=== APP LOG TAIL (newest 12, DB TRUTH — TSV is stale pre-systemd artifact) ==="
$PY - <<'PYEOF'
import sqlite3
c = sqlite3.connect("apply_queue.db")
seen = set()
for r in c.execute("SELECT applied_at, portal, company, role, status FROM applications ORDER BY applied_at DESC LIMIT 40"):
    key = (r[3], r[4])
    if key in seen: continue
    seen.add(key)
    print(f"  {r[0]}\t{r[1]}\t{r[2]}\t{r[3]}\t{r[4]}")
    if len(seen) >= 12: break
PYEOF
echo "=== WORKER LOG TAILS ==="
for f in logs/li-w1.log logs/li-w2.log logs/wf-w1.log logs/wf-w2.log; do
  [ -f "$f" ] && echo "--- $f ---" && tail -3 "$f"
done
echo "=== SESSIONS ==="
$PY - <<'PYEOF'
import json, os
try:
    li = json.load(open("li_state.json"))
    li_at = any(c.get("name")=="li_at" for c in li.get("cookies", []))
    print(f"  linkedin: {len(li.get('cookies',[]))} cookies, li_at={li_at}")
except Exception as e: print("  linkedin state ERR:", e)
try:
    wf = json.load(open("portal_wellfound.json"))
    print(f"  wellfound: {len(wf.get('cookies',[]))} cookies")
except Exception as e: print("  wellfound state ERR:", e)
PYEOF
echo "=== DISK/RAM ==="
df -h / | tail -1 | awk '{print "  disk used", $3, "avail", $4}'
free -h | awk '/Mem:/{print "  ram used", $3, "avail", $7}'
