#!/usr/bin/env bash
# Reset stale claims for a worker before start.
# R4 fix: valid SQLite, NULL-safe, portal-scoped, explicit reason, env DB path.
# Never resets terminal outcomes or uncertain post-send work.
set -u
WORKER="${1:?worker id}"
PORTAL="${2:?portal}"
REASON="${3:?reason required (pass explicit reason, e.g. stale-lease-repair)}"

# Refuse while farm paused.
PAUSE_FILES=(
  "${JOBHUNT_PAUSE_FILE:-/var/lib/jobhunt/FARM_PAUSED}"
  "./.farm_paused"
)
for f in "${PAUSE_FILES[@]}"; do
  if [ -f "$f" ]; then
    echo "ERROR: farm is paused ($f) — not resetting claims. Clear pause first." >&2
    exit 1
  fi
done

DB_PATH="${JOBHUNT_QUEUE_DB:-./apply_queue.db}"
if [ ! -f "$DB_PATH" ]; then
  echo "ERROR: queue DB not found: $DB_PATH (set JOBHUNT_QUEUE_DB)" >&2
  exit 1
fi

# Python binary: allow override, else PATH python3.
PYBIN="${JOBHUNT_PYBIN:-python3}"

"$PYBIN" - "$PORTAL" "$WORKER" "$REASON" "$DB_PATH" <<'PYEOF'
import sqlite3, sys
from datetime import datetime, timezone

portal, worker, reason, db_path = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
c = sqlite3.connect(db_path)

# Never reset terminal or uncertain post-send outcomes.
# NULL-safe: NULL outcome = unknown, resettable only if no submit intent.
rows = c.execute(
    """SELECT id, result, last_outcome_code FROM jobs
    WHERE status='claimed' AND claimed_by=? AND portal=?
    AND (last_outcome_code IS NULL OR last_outcome_code NOT IN
      ('submitted','already_applied','applied','submit-unconfirmed','submitted-unconfirmed'))
    AND (result IS NULL OR result NOT LIKE 'submitted%' AND result NOT LIKE 'applied%' AND result NOT LIKE '%submit-unconfirmed%')""",
    (worker, portal),
).fetchall()

if not rows:
    print(f"no resettable claims for {worker} (portal={portal})")
    c.close()
    sys.exit(0)

now = datetime.now(timezone.utc).isoformat()
reset_ids = []
for row in rows:
    job_id, result, outcome = row
    c.execute(
        """UPDATE jobs SET status='pending', claimed_by=NULL,
           claimed_at=NULL, lease_expires_at=NULL,
           result='reset|' || COALESCE(result, 'unknown'),
           admin_reset_at=?, admin_reset_reason=?
           WHERE id=? AND status='claimed' AND claimed_by=?""",
        (now, f"{reason} (worker={worker},portal={portal})", job_id, worker),
    )
    if c.total_changes > 0:
        reset_ids.append(job_id)

c.commit()
print(f"reset {len(reset_ids)} stale claims for {worker} (portal={portal}): {reset_ids}")
c.close()
PYEOF
