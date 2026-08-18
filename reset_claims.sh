#!/usr/bin/env bash
# Reset stale claims for a worker before start
set -u
WORKER="${1:?worker id}"
PORTAL="${2:?portal}"
/home/ubuntu/jobhunt-venv/bin/python - "$PORTAL" "$WORKER" <<'PYEOF'
import sqlite3, sys
portal, worker = sys.argv[1], sys.argv[2]
c = sqlite3.connect("/home/ubuntu/job_hunt_linkedin/apply_queue.db")
n = c.execute("UPDATE jobs SET status='pending', claimed_by=NULL WHERE status='claimed' AND claimed_by=?", (worker,)).rowcount
c.commit()
print(f"reset {n} stale claims for {worker}")
PYEOF
