#!/bin/bash
export TMPDIR=/home/ubuntu/tmp_chrome
export LI_LOGIN_PROFILE=/home/ubuntu/tmp_chrome/li_login_profile_fresh
cd /home/ubuntu/job_hunt_linkedin
pkill -f "[l]i_relogin" 2>/dev/null
rm -f otp.txt
/home/ubuntu/jobhunt-venv/bin/python li_relogin.py >> logs/li_relogin.log 2>&1
if /home/ubuntu/jobhunt-venv/bin/python -c "import json;d=json.load(open('li_state.json'));print(any(c.get('name')=='li_at' for c in d.get('cookies',[])))" | grep -q True; then
  echo "li_at captured -> starting LinkedIn workers" >> logs/li_relogin.log
  sudo -n systemctl enable --now jobhunt-li@w1.service jobhunt-li@w2.service
fi
