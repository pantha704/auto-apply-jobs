#!/usr/bin/env bash
cd /home/ubuntu/job_hunt_linkedin || exit 1
/home/ubuntu/jobhunt-venv/bin/python -m py_compile worker_wellfound.py && echo "COMPILE OK"
echo "old-dialog-refs: $(grep -c 'role=dialog' worker_wellfound.py)"
echo "apply-dialog-refs: $(grep -c 'APPLY_DIALOG' worker_wellfound.py)"
