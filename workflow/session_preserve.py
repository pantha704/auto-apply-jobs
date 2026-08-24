"""Canonical portal sessions must survive pause/shutdown.

Farm control may only stop processes. It must never unlink, rmtree, or
truncate session bundles, browser profiles, or queue/control DBs.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def protected_roots() -> list[Path]:
    roots = [
        Path(os.getenv("JOBHUNT_SESSION_STORAGE", REPO / ".private" / "sessions")),
        Path(os.getenv("JOBHUNT_QUEUE_DB", "/var/lib/jobhunt/apply_queue.db")),
        Path(os.getenv("JOBHUNT_CONTROL_DB", "/var/lib/jobhunt/controlplane.db")),
        REPO / ".private" / "sessions",
        REPO / "profiles",
        REPO / "li_state.json",
    ]
    seen: set[Path] = set()
    out: list[Path] = []
    for path in roots:
        resolved = path.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(resolved)
    return out


def snapshot() -> dict[str, tuple[int, int]]:
    """path -> (mtime_ns, size) for existing protected files."""
    found: dict[str, tuple[int, int]] = {}
    for root in protected_roots():
        if root.is_file():
            stat = root.stat()
            found[str(root)] = (stat.st_mtime_ns, stat.st_size)
            continue
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            name = path.name.lower()
            if path.suffix == ".enc" or name in {"li_state.json"} or "session" in name:
                stat = path.stat()
                found[str(path)] = (stat.st_mtime_ns, stat.st_size)
    return found


def assert_intact(before: dict[str, tuple[int, int]]) -> dict:
    after = snapshot()
    missing = sorted(path for path in before if path not in after)
    changed = sorted(
        path for path, meta in before.items() if path in after and after[path] != meta
    )
    if missing or changed:
        raise RuntimeError(
            "session/state files were mutated by farm control: "
            f"missing={missing[:8]} changed={changed[:8]}"
        )
    return {"intact": True, "tracked": len(before)}
