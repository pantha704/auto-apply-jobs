#!/usr/bin/env bash
# Persistent LinkedIn relogin loop: keeps retrying until li_at is captured.
# Stops itself once the session is live.
cd /home/ubuntu/job_hunt_linkedin || exit 1
for i in $(seq 1 40); do
  /home/ubuntu/jobhunt-venv/bin/python li_relogin.py >> logs/li_relogin.log 2>&1
  if /home/ubuntu/jobhunt-venv/bin/python -c "
import json
d = json.load(open('li_state.json'))
print('CAPTURED' if any(c.get('name')=='li_at' for c in d.get('cookies', [])) else 'NO')
" | grep -q CAPTURED; then
    echo "LI SESSION CAPTURED after $i attempt(s)"
    exit 0
  fi
  echo "attempt $i failed, retrying in 120s"
  sleep 120
done
echo "gave up after 40 attempts"
exit 1
