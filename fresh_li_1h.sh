#!/usr/bin/env bash
# Hourly LinkedIn collection/injection. Silent only on a successful no-op.
set -uo pipefail
export TMPDIR=/home/ubuntu/tmp_chrome
export JOBHUNT_QUEUE_DB=/var/lib/jobhunt/apply_queue.db
cd /home/ubuntu/job_hunt_linkedin || exit 1
exec 9>/tmp/fresh_li.lock
flock -n 9 || exit 0
PY=/home/ubuntu/jobhunt-venv/bin/python

LI_TPR=r3600 LI_VMAX=1 LI_BUDGET=280 LI_PAUSE_PAGE_MIN=4 LI_PAUSE_PAGE_MAX=6 \
  "$PY" scrape_jobs.py > logs/scrape_fresh_cron.log 2>&1
SC=$?
if [ "$SC" -ne 0 ]; then
  echo "fresh-li-1h: collector failed rc=$SC" >&2
  exit "$SC"
fi

"$PY" - <<'PY' || exit 2
import json, os, time
p='jobs_raw_r3600_india.json'
d=json.load(open(p))
assert isinstance(d.get('jobs'), list)
assert time.time()-os.path.getmtime(p) < 600
PY

"$PY" add_fresh_jobs.py jobs_raw_r3600_india.json --portal linkedin --fresh > /tmp/fresh_add.log 2>&1
AC=$?
if [ "$AC" -ne 0 ]; then
  echo "fresh-li-1h: injector failed rc=$AC" >&2
  exit "$AC"
fi
ADDED=$(grep -m1 -oP '(?<=added )\d+' /tmp/fresh_add.log || true)
ADDED=${ADDED:-0}

PURGE=$($PY - <<'PY'
import sqlite3
from datetime import datetime, timedelta, timezone
cut=(datetime.now(timezone.utc)-timedelta(hours=24)).isoformat()
c=sqlite3.connect('/var/lib/jobhunt/apply_queue.db')
n=c.execute("""DELETE FROM jobs WHERE portal='linkedin' AND status IN ('pending','skip')
 AND (posted_at < ? OR (posted_at IS NULL AND fetched_at < ?) OR
      (posted_at IS NULL AND fetched_at IS NULL))""",(cut,cut)).rowcount
c.commit(); print(n); c.close()
PY
) || exit 3
if [ "$ADDED" -gt 0 ] || [ "$PURGE" -gt 0 ]; then
  PENDING=$($PY - <<'PY'
import sqlite3
c=sqlite3.connect('/var/lib/jobhunt/apply_queue.db')
print(c.execute("SELECT COUNT(*) FROM jobs WHERE portal='linkedin' AND status='pending'").fetchone()[0])
PY
)
  echo "li-fresh-1h: added=$ADDED purged=$PURGE pending=$PENDING"
fi
