#!/usr/bin/env bash
cd /home/ubuntu/job_hunt_linkedin || exit 1
export TMPDIR=/home/ubuntu/tmp_chrome
exec /home/ubuntu/jobhunt-venv/bin/python scrape_jobs.py > logs/scrape_fresh.log 2>&1
