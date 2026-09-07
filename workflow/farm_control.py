"""Farm-wide pause flag and terminal dispatch guard.

Harvest/watchdog honor this so stop stays stopped.
Workers check can_send() before any terminal action (submit, apply, send).
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_FLAG = REPO_ROOT / ".farm_paused"

# F04: Global sending gate — a separate flag that controls terminal actions.
# This is distinct from the farm pause (which pauses everything).
# When SENDING_DISABLED exists, no worker may click Submit/Apply/Send.
SENDING_GATE = REPO_ROOT / ".sending_disabled"


def default_flag() -> Path:
    return Path(os.getenv("JOBHUNT_PAUSE_FLAG", "/var/lib/jobhunt/FARM_PAUSED"))


def flag_paths() -> list[Path]:
    paths = [default_flag()]
    if LOCAL_FLAG.resolve() != paths[0].resolve():
        paths.append(LOCAL_FLAG)
    return paths


def is_paused() -> bool:
    return any(path.is_file() for path in flag_paths())


def pause_reason() -> str:
    for path in flag_paths():
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            return text or f"paused ({path})"
    return ""


def write_paused(reason: str = "paused by operator") -> Path:
    for path in flag_paths():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(reason.rstrip() + "\n", encoding="utf-8")
            os.chmod(path, 0o600)
            return path
        except OSError:
            continue
    raise RuntimeError("could not write farm pause flag")


def clear_paused() -> list[str]:
    cleared: list[str] = []
    for path in flag_paths():
        if path.is_file():
            path.unlink()
            cleared.append(str(path))
    return cleared


# --- F04: Terminal dispatch guard ---

def can_send() -> bool:
    """Check if terminal actions (submit/apply/send) are permitted.

    F04 fix: Workers MUST call this before any terminal action.
    Returns False if:
    - Farm is paused
    - Sending is explicitly disabled via .sending_disabled flag
    """
    if is_paused():
        return False
    if SENDING_GATE.is_file():
        return False
    return True


def disable_sending(reason: str = "sending disabled by operator") -> Path:
    """Explicitly disable terminal actions without pausing the whole farm."""
    try:
        SENDING_GATE.parent.mkdir(parents=True, exist_ok=True)
        SENDING_GATE.write_text(reason.rstrip() + "\n", encoding="utf-8")
        os.chmod(SENDING_GATE, 0o600)
        return SENDING_GATE
    except OSError as exc:
        raise RuntimeError(f"could not write sending gate: {exc}") from exc


def enable_sending() -> bool:
    """Re-enable terminal actions."""
    if SENDING_GATE.is_file():
        SENDING_GATE.unlink()
        return True
    return False


def sending_disabled_reason() -> str:
    """Return the reason sending is disabled, or empty if sending is enabled."""
    if SENDING_GATE.is_file():
        return SENDING_GATE.read_text(encoding="utf-8").strip() or "sending disabled"
    return ""
