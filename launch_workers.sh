#!/usr/bin/env bash
# Launch headless job-hunt workers on VPS (CloakBrowser)
set -u
cd /home/ubuntu/job_hunt_linkedin || exit 1
mkdir -p logs
export TMPDIR=/home/ubuntu/tmp_chrome
# Credentials must be supplied by the private environment (for example,
# /etc/jobhunt/job-hunt.env); never read or export a password from the repo.
: "${WF_PASSWORD:?WF_PASSWORD must be set outside the repository}"
PY=/home/ubuntu/jobhunt-venv/bin/python

pkill -f "[w]orker_linkedin.py" 2>/dev/null
pkill -f "[w]orker_wellfound.py" 2>/dev/null
sleep 2

$PY - <<'PYEOF'
import sqlite3
c = sqlite3.connect("apply_queue.db")
n = c.execute("UPDATE jobs SET status='pending', claimed_by=NULL WHERE status='claimed'").rowcount
c.commit()
print(f"reset {n} stale claims")
PYEOF

setsid nohup $PY worker_linkedin.py li-w1 > logs/li-w1.log 2>&1 &
setsid nohup $PY worker_linkedin.py li-w2 > logs/li-w2.log 2>&1 &
setsid nohup $PY worker_wellfound.py wf-w1 > logs/wf-w1.log 2>&1 &
setsid nohup $PY worker_wellfound.py wf-w2 > logs/wf-w2.log 2>&1 &

sleep 30
echo "=== WORKER PROCS ==="
ps aux | grep -E "[w]orker_(linkedin|wellfound)" | awk '{print $2, $3"%", $11, $12, $13}'
echo "=== QUEUE ==="
$PY -c "
import sqlite3
c = sqlite3.connect('apply_queue.db')
for r in c.execute(\"SELECT portal, status, COUNT(*) FROM jobs GROUP BY portal, status ORDER BY portal\"):
    print(r)
"
echo "=== LOG TAILS ==="
for f in logs/li-w1.log logs/li-w2.log logs/wf-w1.log logs/wf-w2.log; do echo "--- $f ---"; tail -4 "$f"; done
