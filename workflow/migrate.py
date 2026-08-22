#!/usr/bin/env python3
"""Explicit, backup-first workflow schema migration utility."""

from __future__ import annotations

import argparse
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from workflow.schema import migrate_control, migrate_queue


def _counts(path: Path, tables: tuple[str, ...]) -> dict[str, int]:
    db = sqlite3.connect(path)
    try:
        existing = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        return {
            table: db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in tables
            if table in existing
        }
    finally:
        db.close()


def _sqlite_backup(source: Path, target: Path) -> None:
    source_db = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    target_db = sqlite3.connect(target)
    try:
        source_db.backup(target_db)
    finally:
        target_db.close()
        source_db.close()
    target.chmod(0o600)


def _migrate_one(
    source: Path,
    kind: str,
    *,
    dry_run: bool,
    backup_dir: Path,
) -> dict[str, object]:
    if not source.is_file():
        raise FileNotFoundError(source)
    legacy_tables = (
        ("jobs", "applications")
        if kind == "queue"
        else ("sites", "profile_fields", "events", "worker_samples")
    )
    before = _counts(source, legacy_tables)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if dry_run:
        temp_dir = Path(tempfile.mkdtemp(prefix="jobhunt-migrate-"))
        target = temp_dir / source.name
    else:
        backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = backup_dir / f"{source.name}.{stamp}.bak"
    _sqlite_backup(source, target)
    migrate = migrate_queue if kind == "queue" else migrate_control
    applied = migrate(target if dry_run else source)
    migrated_path = target if dry_run else source
    after = _counts(migrated_path, legacy_tables)
    if before != after:
        if not dry_run:
            _sqlite_backup(target, source)
        raise RuntimeError(f"legacy row counts changed for {kind}: {before} -> {after}")
    return {
        "database": kind,
        "source": str(source),
        "mode": "dry-run-copy" if dry_run else "migrated",
        "backup": str(target),
        "applied_versions": applied,
        "legacy_counts": after,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument(
        "--backup-dir", type=Path, default=Path("/var/backups/jobhunt/schema")
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    dry_run = not args.apply
    for result in (
        _migrate_one(args.queue, "queue", dry_run=dry_run, backup_dir=args.backup_dir),
        _migrate_one(
            args.control, "control", dry_run=dry_run, backup_dir=args.backup_dir
        ),
    ):
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
