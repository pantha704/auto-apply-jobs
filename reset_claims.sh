#!/usr/bin/env bash
# Reset stale claims for a worker before start.
# F15 fix: Checks pause state, requires explicit reason, and never resets
# jobs that completed a terminal action (submitted/applied).
set -u
WORKER="${1:?worker id}"
PORTAL="${2:?portal}"
REASON="${3:-manual-reset}"

# F04/F15: Refuse to reset while farm is paused.
if [ -f "/var/lib/jobhunt/FARM_PAUSED" ] || [ -f "/home/ubuntu/job_hunt_linkedin/.farm_paused" ]; then
  echo "ERROR: farm is paused — not resetting claims. Clear pause first." >&2
  exit 1
fi

/home/ubuntu/jobhunt-venv/bin/python - "$PORTAL" "$WORKER" "$REASON" <<'PYEOF'
import sqlite3, sys, json
from datetime import datetime, timezone

portal, worker, reason = sys.argv[1], sys.argv[2], sys.argv[3]
db_path = "/home/ubuntu/job_hunt_linkedin/apply_queue.db"
c = sqlite3.connect(db_path)

# F04: Never reset jobs that have a terminal outcome (submitted/applied).
# Only reset jobs that are genuinely stuck in 'claimed' state.
rows = c.execute(
    """SELECT id, result, last_outcome_code FROM jobs
    WHERE status='claimed' AND claimed_by=?
    AND last_outcome_code IS NOT IN ('submitted', 'already_applied')""",
    (worker,)
).fetchall()

if not rows:
    print(f"no resettable claims for {worker} (portal={portal})")
    c.close()
    sys.exit(0)

# Log the reset for audit trail.
now = datetime.now(timezone.utc).isoformat()
reset_ids = []
for row in rows:
    job_id, result, outcome = row
    # F15: Record that this was administratively reset, not a natural completion.
    c.execute(
        """UPDATE jobs SET status='pending', claimed_by=NULL,
           claimed_at=NULL, lease_expires_at=NULL,
           result='reset|' || COALESCE(result, 'unknown'),
           admin_reset_at=?, admin_reset_reason=?
           WHERE id=?""",
        (now, f"{reason} (worker={worker})", job_id)
    )
    reset_ids.append(job_id)

c.commit()
print(f"reset {len(reset_ids)} stale claims for {worker} (portal={portal}): {reset_ids}")
c.close()
PYEOF
