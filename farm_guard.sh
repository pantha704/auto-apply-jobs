# Honor farm pause. Source from repo scripts; exit 0 is silent.
if [ -f "${JOBHUNT_PAUSE_FLAG:-/var/lib/jobhunt/FARM_PAUSED}" ] || \
   [ -f /home/ubuntu/job_hunt_linkedin/.farm_paused ]; then
  exit 0
fi
