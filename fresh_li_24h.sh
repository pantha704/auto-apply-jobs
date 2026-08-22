#!/usr/bin/env bash
# Six-hour LinkedIn 24h fallback collection/injection.
set -uo pipefail
export TMPDIR=/home/ubuntu/tmp_chrome
export JOBHUNT_QUEUE_DB=/var/lib/jobhunt/apply_queue.db
cd /home/ubuntu/job_hunt_linkedin || exit 1
exec 9>/tmp/fresh_li.lock
flock -n 9 || exit 0
PY=/home/ubuntu/jobhunt-venv/bin/python

LI_TPR=r86400 LI_VMAX=2 LI_BUDGET=280 LI_PAUSE_PAGE_MIN=4 LI_PAUSE_PAGE_MAX=6 \
  "$PY" scrape_jobs.py > logs/scrape_fresh_24h.log 2>&1
SC=$?
if [ "$SC" -ne 0 ]; then
  echo "fresh-li-24h: collector failed rc=$SC" >&2
  exit "$SC"
fi
"$PY" - <<'PY' || exit 2
import json, os, time
p='jobs_raw_r86400_india.json'; d=json.load(open(p))
assert isinstance(d.get('jobs'),list)
assert time.time()-os.path.getmtime(p)<600
PY
"$PY" add_fresh_jobs.py jobs_raw_r86400_india.json --portal linkedin --fresh > /tmp/fresh_add_24h.log 2>&1
AC=$?
if [ "$AC" -ne 0 ]; then
  echo "fresh-li-24h: injector failed rc=$AC" >&2
  exit "$AC"
fi
ADDED=$(grep -m1 -oP '(?<=added )\d+' /tmp/fresh_add_24h.log || true)
ADDED=${ADDED:-0}
[ "$ADDED" -gt 0 ] && echo "li-fresh-24h: added=$ADDED"
