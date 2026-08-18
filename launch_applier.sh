#!/usr/bin/env bash
# Cleanup orphaned Profile-4 chrome + relaunch applier on given URL
set -u
URL="${1:?usage: launch_applier.sh <url>}"
cd /home/panther/Documents/job_hunt_linkedin
# kill old applier + any chrome using Profile 4
pkill -f "^python3 applier" 2>/dev/null
pkill -f "user-data-dir=.*Profile 4" 2>/dev/null
sleep 3
rm -f "/home/panther/.config/google-chrome/Profile 4/SingletonLock" \
      "/home/panther/.config/google-chrome/Profile 4/SingletonSocket" \
      "/home/panther/.config/google-chrome/Profile 4/SingletonCookie" 2>/dev/null
rm -f /tmp/applier_cmd.json /tmp/applier_out.json
echo "$URL" > /tmp/current_job.txt
nohup python3 applier.py "$URL" > /tmp/applier_session.log 2>&1 &
echo "launched PID $!"
sleep 4
ps aux | grep -c "[a]pplier.py"
