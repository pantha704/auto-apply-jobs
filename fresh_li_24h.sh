#!/usr/bin/env bash
# 6-hourly fallback pass: 24h-window LinkedIn jobs (last resort pool), gentle.
# RESUMABLE — same checkpoint-preserving design as fresh_li_1h.sh.
set -u
export TMPDIR=/home/ubuntu/tmp_chrome
cd /home/ubuntu/job_hunt_linkedin || exit 1
exec 9>/tmp/fresh_li.lock
flock -n 9 || exit 0

LI_TPR=r86400 LI_VMAX=2 LI_BUDGET=280 LI_PAUSE_PAGE_MIN=4 LI_PAUSE_PAGE_MAX=6 \
  /home/ubuntu/jobhunt-venv/bin/python scrape_jobs.py > logs/scrape_fresh_24h.log 2>&1
SC=$?
/home/ubuntu/jobhunt-venv/bin/python add_fresh_jobs.py jobs_raw_r86400_india.json > /tmp/fresh_add_24h.log 2>&1
grep -q "added" /tmp/fresh_add_24h.log && tail -1 /tmp/fresh_add_24h.log
[ "$SC" -ne 0 ] && [ "$SC" -ne 124 ] && echo "WARN: scrape exit=$SC (checkpoint preserved)" >&2
exit 0
