#!/usr/bin/env python3
"""Stop or resume the apply farm until an operator reactivates it.

  python jobhunt_farm.py status
  python jobhunt_farm.py pause
  python jobhunt_farm.py resume

Does not stop the dashboard. Does not delete queue, sessions, or proof.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone

from workflow.farm_control import clear_paused, is_paused, pause_reason, write_paused

APPLY_UNITS = [
    "jobhunt-li@w1.service",
    "jobhunt-li@w2.service",
    "jobhunt-wf@w1.service",
    "jobhunt-wf@w2.service",
    "jobhunt-is@is-w1.service",
    "jobhunt-yc@w1.service",
    "jobhunt-ext@w1.service",
    "jobhunt-email@w1.service",
    "jobhunt-review@r1.service",
]

KEEP_RUNNING = [
    "jobhunt-dashboard.service",
]


def _systemctl(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sudo", "systemctl", *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _unit_state(unit: str) -> str:
    p = subprocess.run(
        ["systemctl", "is-active", unit],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return (p.stdout or p.stderr or "unknown").strip() or "unknown"


def cmd_status() -> int:
    paused = is_paused()
    print(f"farm: {'PAUSED' if paused else 'RUNNING'}")
    if paused:
        print(f"reason: {pause_reason()}")
    print("apply units:")
    for unit in APPLY_UNITS:
        print(f"  {unit}\t{_unit_state(unit)}")
    print("kept running:")
    for unit in KEEP_RUNNING:
        print(f"  {unit}\t{_unit_state(unit)}")
    return 0


def cmd_pause() -> int:
    stamp = datetime.now(timezone.utc).isoformat()
    path = write_paused(f"paused at {stamp} UTC by jobhunt_farm.py")
    print(f"flag {path}")
    stop = _systemctl(["disable", "--now", *APPLY_UNITS])
    if stop.returncode != 0:
        print((stop.stderr or stop.stdout).strip()[:400], file=sys.stderr)
        print("flag is set; harvest/watchdog will stay quiet even if a unit is still up")
        return 1
    print("apply workers disabled until resume")
    return cmd_status()


def cmd_resume() -> int:
    cleared = clear_paused()
    print("cleared", ", ".join(cleared) or "(no flag)")
    start = _systemctl(["enable", "--now", *APPLY_UNITS])
    if start.returncode != 0:
        print((start.stderr or start.stdout).strip()[:400], file=sys.stderr)
        return 1
    print("apply workers enabled")
    return cmd_status()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "pause", "resume"))
    args = parser.parse_args()
    if args.command == "status":
        return cmd_status()
    if args.command == "pause":
        return cmd_pause()
    return cmd_resume()


if __name__ == "__main__":
    raise SystemExit(main())
