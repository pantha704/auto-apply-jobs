"""Tests for SubmissionFinalizer — atomic queue + audit writes."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest


def _queue_db(path: Path) -> None:
    """Create the minimal queue schema needed for SubmissionFinalizer."""
    db = sqlite3.connect(path)
    db.execute(
        """CREATE TABLE jobs (
        id TEXT PRIMARY KEY, portal TEXT, url TEXT, title TEXT, source TEXT,
        status TEXT DEFAULT 'pending', claimed_by TEXT, result TEXT,
        prio INTEGER DEFAULT 0, posted_at TEXT, fetched_at TEXT
        )"""
    )
    db.execute(
        """CREATE TABLE applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT, portal TEXT NOT NULL,
        company TEXT, role TEXT, url TEXT NOT NULL, applied_at TEXT NOT NULL,
        answers TEXT, resume_used TEXT, status TEXT, note TEXT,
        snap_before TEXT, snap_after TEXT, url_hash TEXT,
        UNIQUE(portal, url_hash)
        )"""
    )
    db.execute(
        "INSERT INTO jobs(id,portal,url,title,status) "
        "VALUES('j1','linkedin','https://example.com/job/1','Engineer','pending')"
    )
    db.commit()
    db.close()


class TestSubmissionFinalizer:
    def test_finalize_inserts_audit_and_updates_job(self, tmp_path):
        from audit import SubmissionFinalizer

        db_path = tmp_path / "q.db"
        _queue_db(db_path)
        fin = SubmissionFinalizer(str(db_path))

        fin.finalize(
            job_id="j1",
            status="done",
            result="submitted",
            portal="linkedin",
            company="Acme",
            role="Engineer",
            url="https://example.com/job/1",
            audit_status="submitted",
            answers={"phone": "555-0000"},
            resume_used="resume.pdf",
            note="applied",
        )

        db = sqlite3.connect(db_path)
        job = db.execute("SELECT status, result FROM jobs WHERE id='j1'").fetchone()
        assert job == ("done", "submitted")

        app = db.execute(
            "SELECT portal, company, role, status FROM applications WHERE url_hash=?",
            (hashlib.sha1(b"https://example.com/job/1").hexdigest(),),
        ).fetchone()
        assert app == ("linkedin", "Acme", "Engineer", "submitted")
        db.close()

    def test_finalize_skip_records_audit(self, tmp_path):
        """Skip outcomes also get an audit record (matching original behavior)."""
        from audit import SubmissionFinalizer

        db_path = tmp_path / "q.db"
        _queue_db(db_path)
        fin = SubmissionFinalizer(str(db_path))

        fin.finalize(
            job_id="j1",
            status="skip",
            result="closed",
            portal="linkedin",
            company="",
            role="Engineer",
            url="https://example.com/job/1",
            audit_status="skipped:closed",
        )

        db = sqlite3.connect(db_path)
        job = db.execute("SELECT status, result FROM jobs WHERE id='j1'").fetchone()
        assert job == ("skip", "closed")
        app = db.execute("SELECT status FROM applications").fetchone()
        assert app[0] == "skipped:closed"
        db.close()

    def test_finalize_is_atomic_on_failure(self, tmp_path):
        """Orphan finalize must refuse and roll back audit insert."""
        from audit import SubmissionFinalizer
        import pytest

        db_path = tmp_path / "q.db"
        _queue_db(db_path)
        fin = SubmissionFinalizer(str(db_path))

        # Non-existent job_id — must raise, no orphan audit row.
        with pytest.raises(ValueError, match="unknown job"):
            fin.finalize(
                job_id="nonexistent",
                status="done",
                result="submitted",
                portal="linkedin",
                company="Test",
                role="Engineer",
                url="https://example.com/job/2",
                audit_status="submitted",
            )

        db = sqlite3.connect(db_path)
        count = db.execute("SELECT count(*) FROM applications").fetchone()[0]
        assert count == 0
        db.close()

    def test_finalize_deduplicates_audit(self, tmp_path):
        """Second finalize for same URL should update, not duplicate."""
        from audit import SubmissionFinalizer

        db_path = tmp_path / "q.db"
        _queue_db(db_path)
        fin = SubmissionFinalizer(str(db_path))

        fin.finalize(
            job_id="j1",
            status="done",
            result="submitted",
            portal="linkedin",
            company="Acme",
            role="Engineer",
            url="https://example.com/job/1",
            audit_status="submit-unconfirmed",
        )
        fin.finalize(
            job_id="j1",
            status="done",
            result="submitted",
            portal="linkedin",
            company="Acme",
            role="Engineer",
            url="https://example.com/job/1",
            audit_status="submitted",
            note="confirmed on retry",
        )

        db = sqlite3.connect(db_path)
        count = db.execute("SELECT count(*) FROM applications").fetchone()[0]
        assert count == 1
        status = db.execute("SELECT status, note FROM applications").fetchone()
        assert status == ("submitted", "confirmed on retry")
        db.close()

    def test_finalize_without_url_skips_audit(self, tmp_path):
        """If no URL is provided, audit insert is skipped gracefully."""
        from audit import SubmissionFinalizer

        db_path = tmp_path / "q.db"
        _queue_db(db_path)
        fin = SubmissionFinalizer(str(db_path))

        fin.finalize(
            job_id="j1",
            status="done",
            result="submitted",
            portal="linkedin",
            company="Acme",
            role="Engineer",
            url="",
            audit_status="submitted",
        )

        db = sqlite3.connect(db_path)
        job = db.execute("SELECT status FROM jobs WHERE id='j1'").fetchone()
        assert job[0] == "done"
        count = db.execute("SELECT count(*) FROM applications").fetchone()[0]
        assert count == 0
        db.close()
