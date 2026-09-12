#!/usr/bin/env bash
set -euo pipefail
WORKER="${1:?worker id}"
PORTAL="${2:?portal}"
REASON="${3:?explicit repair reason}"
cd "$(dirname "$0")"
"${JOBHUNT_PYBIN:-python3}" - "$WORKER" "$PORTAL" "$REASON" <<'PY'
import os,sys
from workflow.dispatch import repair_expired
print(repair_expired(os.environ.get('JOBHUNT_QUEUE_DB','apply_queue.db'),worker_id=sys.argv[1],portal=sys.argv[2],reason=sys.argv[3]))
PY
