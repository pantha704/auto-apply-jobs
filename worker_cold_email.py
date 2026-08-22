#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from workflow.cold_email import ColdEmailQueue, ColdEmailSender, EventJournal
from workflow.gmail_provider import GmailApiProvider
from workflow.schema import migrate_control
from workflow.worker_telemetry import telemetry_for

ROOT = Path(__file__).resolve().parent


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_status(path: Path, worker_id: str, status: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"worker_id": worker_id, "status": status,
                               "updated_at": utc_now()}, separators=(",", ":")))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def build_sender(worker_id: str) -> tuple[ColdEmailSender, Path]:
    db = Path(os.getenv("JOBHUNT_CONTROL_DB", ROOT / "controlplane.db"))
    state_root = Path(os.getenv("JOBHUNT_WORKER_STATE", ROOT / "state_queue"))
    worker_root = state_root / "cold-email" / worker_id
    token = Path(os.getenv("JOBHUNT_GOOGLE_TOKEN", "/home/ubuntu/.hermes/google_token.json"))
    api_script = Path(os.getenv(
        "JOBHUNT_GOOGLE_API_SCRIPT",
        "/home/ubuntu/.hermes/skills/productivity/google-workspace/scripts/google_api.py",
    ))
    migrate_control(db)
    queue = ColdEmailQueue(db)
    provider = GmailApiProvider(token, api_script)
    journal = EventJournal(worker_root / "events.jsonl")
    return ColdEmailSender(queue, provider, journal, worker_id), worker_root / "status.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Approved cold-email Gmail sender")
    parser.add_argument("worker_id", nargs="?", default="email-w1")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    sender, status_path = build_sender(args.worker_id)
    queue_db = os.getenv("JOBHUNT_QUEUE_DB", str(ROOT / "apply_queue.db"))
    state_root = os.getenv("JOBHUNT_WORKER_STATE", str(ROOT / "state_queue"))
    runtime = telemetry_for(args.worker_id, "cold-email", queue_db, state_root)
    idle_seconds = max(5, int(os.getenv("JOBHUNT_EMAIL_IDLE_SECONDS", "30")))
    send_interval = max(30, int(os.getenv("JOBHUNT_EMAIL_SEND_INTERVAL", "60")))
    while True:
        result = sender.run_once()
        write_status(status_path, args.worker_id, result)
        if result == "provider_not_authenticated":
            runtime.blocked(result)
        elif result == "sent":
            runtime.outcome("approved-email", "sent", "provider-confirmed")
        elif result in {"idle", "queue_empty"}:
            runtime.idle()
        else:
            runtime.blocked(result)
        print(json.dumps({"timestamp": utc_now(), "worker": args.worker_id,
                          "status": result}), flush=True)
        if args.once:
            return 0
        time.sleep(send_interval if result == "sent" else idle_seconds)


if __name__ == "__main__":
    sys.exit(main())
