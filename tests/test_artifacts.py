from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from workflow.schema import migrate_queue


def queue_fixture(path):
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE jobs (
        id TEXT PRIMARY KEY, portal TEXT, url TEXT, title TEXT, source TEXT,
        status TEXT DEFAULT 'pending', claimed_by TEXT, result TEXT,
        prio INTEGER DEFAULT 0, posted_at TEXT, fetched_at TEXT
    )""")
    db.execute("""CREATE TABLE applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT, portal TEXT NOT NULL,
        company TEXT, role TEXT, url TEXT NOT NULL, applied_at TEXT NOT NULL,
        answers TEXT, resume_used TEXT, status TEXT, note TEXT,
        snap_before TEXT, snap_after TEXT, url_hash TEXT,
        UNIQUE(portal, url_hash)
    )""")
    db.execute("INSERT INTO jobs(id,portal,url,title,status) VALUES('j','generic','u','t','pending')")
    db.commit()
    db.close()
    migrate_queue(path)
    db = sqlite3.connect(path)
    db.execute("INSERT INTO application_runs(id,job_id,adapter,lease_token,state,started_at,confirmed) VALUES('r','j','generic','lease','running','2026-08-19T00:00:00+00:00',0)")
    db.commit()
    db.close()


def test_store_registers_hash_size_and_private_permissions(tmp_path):
    from workflow.artifacts import ArtifactStore

    db_path = tmp_path / "queue.db"
    queue_fixture(db_path)
    root = tmp_path / "private"
    store = ArtifactStore(db_path, root, allowed_roots=[root])

    artifact = store.store("r", "screenshots/result.png", b"secret", kind="screenshot", pii_class="sensitive")

    assert artifact.sha256 == "2bb80d537b1da3e38bd30361aa855686bde0eacd7162fef6a25fe97bf527a25b"
    assert artifact.size_bytes == 6
    assert artifact.path == root / "screenshots/result.png"
    assert artifact.path.read_bytes() == b"secret"
    assert os.stat(root).st_mode & 0o777 == 0o700
    assert os.stat(artifact.path.parent).st_mode & 0o777 == 0o700
    assert os.stat(artifact.path).st_mode & 0o777 == 0o600
    row = sqlite3.connect(db_path).execute("SELECT run_id,kind,path,sha256,size_bytes,pii_class FROM artifacts WHERE id=?", (artifact.id,)).fetchone()
    assert row == ("r", "screenshot", str(artifact.path), artifact.sha256, 6, "sensitive")


def test_paths_cannot_traverse_or_escape_through_symlinks(tmp_path):
    from workflow.artifacts import ArtifactStore

    db_path = tmp_path / "queue.db"
    queue_fixture(db_path)
    root = tmp_path / "private"
    outside = tmp_path / "outside"
    outside.mkdir()
    root.mkdir(mode=0o700)
    (root / "link").symlink_to(outside, target_is_directory=True)
    store = ArtifactStore(db_path, root, allowed_roots=[root])

    with pytest.raises(ValueError):
        store.store("r", "../escape", b"no", kind="x", pii_class="none")
    with pytest.raises(ValueError):
        store.store("r", "link/escape", b"no", kind="x", pii_class="none")
    assert not (outside / "escape").exists()


def test_reads_are_redacted_unless_sensitive_access_is_approved(tmp_path):
    from workflow.artifacts import ArtifactStore, SensitiveAccessDenied

    db_path = tmp_path / "queue.db"
    queue_fixture(db_path)
    root = tmp_path / "private"
    store = ArtifactStore(db_path, root, allowed_roots=[root])
    artifact = store.store("r", "answer.txt", b"phone: 555-0100", kind="answer", pii_class="sensitive")

    assert store.read(artifact.id) == b"[REDACTED]"
    with pytest.raises(SensitiveAccessDenied):
        store.read(artifact.id, sensitive=True)
    store.approve_sensitive_access(artifact.id)
    assert store.read(artifact.id, sensitive=True) == b"phone: 555-0100"


def test_purge_deletes_expired_files_and_metadata_only(tmp_path):
    from workflow.artifacts import ArtifactStore

    db_path = tmp_path / "queue.db"
    queue_fixture(db_path)
    root = tmp_path / "private"
    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    store = ArtifactStore(db_path, root, allowed_roots=[root], now=lambda: now)
    expired = store.store("r", "old", b"old", kind="x", pii_class="none", retain_until=now - timedelta(seconds=1))
    current = store.store("r", "new", b"new", kind="x", pii_class="none", retain_until=now + timedelta(days=1))

    assert store.purge_expired() == 1
    assert not expired.path.exists()
    assert current.path.exists()
    assert sqlite3.connect(db_path).execute("SELECT COUNT(*) FROM artifacts WHERE id=?", (expired.id,)).fetchone()[0] == 0
