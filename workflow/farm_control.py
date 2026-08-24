"""Farm-wide pause flag. Harvest/watchdog honor this so stop stays stopped."""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_FLAG = REPO_ROOT / ".farm_paused"


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
