from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from workflow.schema import migrate_queue


def queue_fixture(path):
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
        "INSERT INTO jobs(id,portal,url,title,status,prio) VALUES('job-1','generic','https://example.test/1','Engineer','pending',10)"
    )
    db.commit()
    db.close()
    migrate_queue(path)


def test_claim_is_exclusive_and_expired_lease_is_reclaimable(tmp_path):
    from workflow.store import WorkflowStore

    path = tmp_path / "queue.db"
    queue_fixture(path)
    clock = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)
    first = WorkflowStore(path, now=lambda: clock)

    claim = first.claim_next("worker-a", portal="generic", lease_seconds=60)
    assert claim is not None
    assert claim.job_id == "job-1"
    assert claim.attempt_count == 1
    assert first.claim_next("worker-b", portal="generic", lease_seconds=60) is None

    later = WorkflowStore(path, now=lambda: clock + timedelta(seconds=61))
    reclaimed = later.claim_next("worker-b", portal="generic", lease_seconds=60)
    assert reclaimed is not None
    assert reclaimed.job_id == "job-1"
    assert reclaimed.attempt_count == 2

    db = sqlite3.connect(path)
    assert db.execute(
        "SELECT state,outcome_code FROM application_runs WHERE id=?", (claim.run_id,)
    ).fetchone() == ("failed", "lease_expired")
    assert db.execute(
        "SELECT outcome_code,retryable FROM job_attempts WHERE run_id=?",
        (claim.run_id,),
    ).fetchone() == ("lease_expired", 1)
    assert db.execute(
        "SELECT state FROM application_runs WHERE id=?", (reclaimed.run_id,)
    ).fetchone() == ("running",)
    db.close()


def test_heartbeat_and_finish_require_lease_owner_and_dual_write(tmp_path):
    from workflow.models import SubmissionEvidence
    from workflow.store import LeaseConflict, WorkflowStore

    path = tmp_path / "queue.db"
    queue_fixture(path)
    clock = datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc)
    store = WorkflowStore(path, now=lambda: clock)
    claim = store.claim_next("worker-a", portal="generic", lease_seconds=60)
    assert claim is not None

    try:
        store.heartbeat(claim.run_id, "worker-a", "wrong-token", lease_seconds=60)
    except LeaseConflict:
        pass
    else:
        raise AssertionError("wrong lease token must fail")

    try:
        store.heartbeat(
            claim.run_id, "worker-b", claim.lease_token, lease_seconds=60
        )
    except LeaseConflict:
        pass
    else:
        raise AssertionError("non-owner heartbeat must fail")

    store.heartbeat(
        claim.run_id, "worker-a", claim.lease_token, lease_seconds=120
    )
    evidence = SubmissionEvidence(
        observed_at=clock.isoformat(),
        success_text="Application submitted",
        application_id="fixture-application-1",
        artifact_ids=(),
    )
    store.confirm_submission(
        claim.run_id,
        "worker-a",
        claim.lease_token,
        portal="generic",
        company="Example",
        role="Engineer",
        url="https://example.test/1",
        evidence=evidence,
    )

    db = sqlite3.connect(path)
    assert db.execute("SELECT status,result FROM jobs WHERE id='job-1'").fetchone() == (
        "done",
        "submitted",
    )
    assert db.execute(
        "SELECT state,confirmed,outcome_code FROM application_runs WHERE id=?",
        (claim.run_id,),
    ).fetchone() == ("submitted", 1, "submitted")
    assert db.execute(
        "SELECT portal,company,role,status FROM applications"
    ).fetchall() == [("generic", "Example", "Engineer", "submitted")]
    db.close()


def test_generic_finish_cannot_assert_submission(tmp_path):
    import pytest

    from workflow.models import Outcome, OutcomeCode
    from workflow.store import WorkflowStore

    path = tmp_path / "queue.db"
    queue_fixture(path)
    store = WorkflowStore(path)
    claim = store.claim_next("worker-a", portal="generic")
    assert claim is not None

    with pytest.raises(ValueError, match="confirm_submission"):
        store.finish(
            claim.run_id,
            claim.worker_id,
            claim.lease_token,
            Outcome(
                code=OutcomeCode.SUBMITTED,
                confirmed=True,
                retryable=False,
            ),
        )
