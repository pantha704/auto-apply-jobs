from __future__ import annotations

import sqlite3
from pathlib import Path

from workflow.migrate import _migrate_one


def test_dry_run_uses_sqlite_backup_and_preserves_wal_rows(tmp_path):
    source = tmp_path / "queue.db"
    writer = sqlite3.connect(source)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.executescript(
        """
        CREATE TABLE jobs (
          id TEXT PRIMARY KEY, portal TEXT, title TEXT, company TEXT,
          location TEXT, url TEXT NOT NULL, prio INTEGER DEFAULT 0,
          status TEXT DEFAULT 'pending', result TEXT, added_at TEXT,
          updated_at TEXT, claimed_by TEXT
        );
        CREATE TABLE applications (
          id INTEGER PRIMARY KEY, portal TEXT, company TEXT, role TEXT,
          status TEXT, applied_at TEXT, url TEXT
        );
        """
    )
    writer.execute(
        "INSERT INTO jobs(id,url,status) VALUES('wal-job','https://example.test','pending')"
    )
    writer.execute(
        """INSERT INTO applications
        (portal,company,role,status,applied_at,url)
        VALUES('fixture','Example','Engineer','submitted','2026-08-19','https://example.test')"""
    )
    writer.commit()

    result = _migrate_one(
        source, "queue", dry_run=True, backup_dir=tmp_path / "unused"
    )
    assert result["legacy_counts"] == {"jobs": 1, "applications": 1}
    migrated = Path(str(result["backup"]))
    db = sqlite3.connect(migrated)
    assert db.execute("SELECT id FROM jobs").fetchall() == [("wal-job",)]
    assert db.execute(
        "SELECT version FROM schema_migrations WHERE database_name='queue'"
    ).fetchall() == [(1,)]
    db.close()
    writer.close()
