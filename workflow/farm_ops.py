"""Shared farm pause / resume / shutdown used by CLI and the dashboard."""
from __future__ import annotations

import os
import subprocess
import threading
from datetime import datetime, timezone

from workflow.farm_control import clear_paused, is_paused, pause_reason, write_paused
from workflow.session_preserve import assert_intact, snapshot

APPLY_UNITS = (
    "jobhunt-is@is-w1.service",
    "jobhunt-li@w1.service",
    "jobhunt-li@w2.service",
    "jobhunt-wf@w1.service",
    "jobhunt-wf@w2.service",
    "jobhunt-yc@w1.service",
    "jobhunt-ext@w1.service",
    "jobhunt-email@w1.service",
    "jobhunt-review@r1.service",
)

SHUTDOWN_UNITS = (
    "jobhunt-session-probe.timer",
    "jobhunt-session-probe.service",
    "jobhunt-li-renew.service",
    "jobhunt-dashboard.service",
)

KEEP_ALIVE = ("jobhunt-dashboard.service",)


def _systemctl(action: str, unit: str) -> str:
    try:
        result = subprocess.run(
            ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", action, unit],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except (OSError,subprocess.TimeoutExpired):
        return "error: service command unavailable or timed out"

    if result.returncode == 0:
        return action
    err = (result.stderr or result.stdout or "systemctl failed").strip()[:200]
    return f"error: {err}"


def unit_active(unit: str) -> str:
    try:
        result = subprocess.run(
            ["/usr/bin/systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError,subprocess.TimeoutExpired):
        return "error: service command unavailable or timed out"

    return (result.stdout or "unknown").strip() or "unknown"


def status() -> dict:
    return {
        "paused": is_paused(),
        "reason": pause_reason(),
        "apply_units": {unit: unit_active(unit) for unit in APPLY_UNITS},
        "dashboard": unit_active("jobhunt-dashboard.service"),
    }


def pause(reason: str | None = None) -> dict:
    before = snapshot()
    stamp = datetime.now(timezone.utc).isoformat()
    path = write_paused(reason or f"paused at {stamp} UTC")
    units = {unit: _systemctl("stop", unit) for unit in APPLY_UNITS}
    preserved = assert_intact(before)
    return {
        "ok": all(value=="stop" for value in units.values()),
        "action": "pause",
        "flag": str(path),
        "sessions_preserved": preserved,
        **status(),
        "unit_results": units,
    }


def resume() -> dict:
    cleared = clear_paused()
    units = {unit: _systemctl("start", unit) for unit in APPLY_UNITS}
    return {"ok": all(value=="start" for value in units.values()), "action": "resume", "cleared": cleared, **status(), "unit_results": units}


def shutdown() -> dict:
    before = snapshot()
    paused = pause("shutdown at operator request")
    extras = {unit: _systemctl("stop", unit) for unit in SHUTDOWN_UNITS if unit != "jobhunt-dashboard.service"}
    delay = float(os.getenv("JOBHUNT_DASHBOARD_SHUTDOWN_DELAY", "2"))

    def _stop_dashboard() -> str:
        return _systemctl("stop", "jobhunt-dashboard.service")

    if delay <= 0:
        extras["jobhunt-dashboard.service"] = _stop_dashboard() or "stop"
    else:
        extras["jobhunt-dashboard.service"] = f"scheduled {delay:.0f}s"
        threading.Timer(delay, _stop_dashboard).start()
    preserved = assert_intact(before)
    return {**paused,"ok":paused["ok"] and all(not value.startswith("error:") for value in extras.values()),"action":"shutdown","extra_results":extras,"dashboard_stop_pending":delay>0,"sessions_preserved":preserved}
