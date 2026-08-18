#!/usr/bin/env bash
# Hourly fresh-research pass: last-hour LinkedIn jobs only, gentle pacing.
# RESUMABLE: checkpoint file is NEVER deleted — the scraper saves per-keyword
# progress and exits cleanly on its time budget; the next run resumes where it
# left off. Jobs are injected even if the scrape hit its budget (dedup makes
# re-injection harmless).
# Silent unless it adds jobs or purges something. Shared lock with the 24h pass.
set -u
export TMPDIR=/home/ubuntu/tmp_chrome
cd /home/ubuntu/job_hunt_linkedin || exit 1
exec 9>/tmp/fresh_li.lock
flock -n 9 || exit 0

LI_TPR=r3600 LI_VMAX=1 LI_BUDGET=280 LI_PAUSE_PAGE_MIN=4 LI_PAUSE_PAGE_MAX=6 \
  /home/ubuntu/jobhunt-venv/bin/python scrape_jobs.py > logs/scrape_fresh_cron.log 2>&1
SC=$?
# inject whatever was collected (file may be a resume checkpoint — dedup handles it)
/home/ubuntu/jobhunt-venv/bin/python add_fresh_jobs.py jobs_raw_r3600_india.json > /tmp/fresh_add.log 2>&1
AC=$?

/home/ubuntu/jobhunt-venv/bin/python - <<'EOF'
import sqlite3
from datetime import datetime, timedelta, timezone
db = "/home/ubuntu/job_hunt_linkedin/apply_queue.db"
cut = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
c = sqlite3.connect(db)
n = c.execute("""DELETE FROM jobs WHERE portal='linkedin' AND status IN ('pending','skip')
                 AND ((posted_at IS NOT NULL AND posted_at < ?) OR (posted_at IS NULL AND fetched_at IS NULL))""", (cut,)).rowcount
c.commit()
p = c.execute("SELECT COUNT(*) FROM jobs WHERE portal='linkedin' AND status='pending'").fetchone()[0]
f1 = c.execute("SELECT COUNT(*) FROM jobs WHERE portal='linkedin' AND status='pending' AND posted_at > ?", (cut,)).fetchone()[0]
c.close()
# stdout = message body; empty = silent (no_agent cron)
if n or "added" in open("/tmp/fresh_add.log").read():
    print(f"li-fresh: purged {n} stale, pending={p}")
EOF
[ "$SC" -ne 0 ] && [ "$SC" -ne 124 ] && echo "WARN: scrape exit=$SC (checkpoint preserved)" >&2
exit 0
