"""Failure injection against the migrated, durable submission boundary."""
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from audit import SubmissionFinalizer
from workflow import application_gate as gate
from workflow import dispatch
from workflow.schema import migrate_queue

URL = 'https://example.com/job/1'


@pytest.fixture
def claimed(tmp_path, monkeypatch):
    path = tmp_path / 'queue.db'
    migrate_queue(path)
    resume = tmp_path / 'resume.pdf'
    resume.write_bytes(b'approved resume')
    runtime = SimpleNamespace(profile_id='p', profile_revision=1, resume_id='r',
        resume_path=str(resume), preference_set=SimpleNamespace(id='policy', version=1),
        session_revision=0, facts={'name':'Applicant'})
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO jobs(id,portal,url,title,status) VALUES('j','linkedin',?,'Engineer','pending')", (URL,))
        assert gate.pin_claim(db, 'j', 'worker', runtime) == 1
    monkeypatch.setattr(dispatch, 'require_can_send', lambda portal: None)
    monkeypatch.setattr(gate, 'published_runtime', lambda *a, **kw: runtime)
    yield path, gate.claim_fence('j'), runtime
    gate.forget_claim('j')


def begin(claimed, **kwargs):
    path, token, _ = claimed
    return dispatch.begin_dispatch(path, 'j', 'worker', token, portal='linkedin', url=URL, **kwargs)


def finish(claimed, **overrides):
    path, token, _ = claimed
    args = dict(job_id='j', worker_id='worker', fence_token=token, status='done',
                result='submitted', portal='linkedin', url=URL, audit_status='submitted')
    args.update(overrides)
    return SubmissionFinalizer(str(path)).finalize(**args)


def test_concurrent_dispatch_commits_only_one_intent(claimed):
    def attempt(_):
        try:
            begin(claimed)
            return True
        except dispatch.DispatchBlocked:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [False, True]
    with sqlite3.connect(claimed[0]) as db:
        assert db.execute('SELECT count(*) FROM submission_intents').fetchone()[0] == 1


@pytest.mark.parametrize('expiry', [None, 'invalid', '2000-01-01T00:00:00+00:00'])
def test_expired_or_invalid_lease_cannot_dispatch(claimed, expiry):
    with sqlite3.connect(claimed[0]) as db:
        db.execute('UPDATE jobs SET lease_expires_at=?', (expiry,))
    with pytest.raises(dispatch.DispatchBlocked):
        begin(claimed)


def test_wrong_identity_cannot_dispatch(claimed):
    with pytest.raises(dispatch.DispatchBlocked):
        dispatch.begin_dispatch(claimed[0], 'j', 'worker', claimed[1], portal='linkedin', url=URL+'wrong')


def test_pause_does_not_leave_intent(claimed, monkeypatch):
    def paused(portal):
        raise RuntimeError('paused')
    monkeypatch.setattr(dispatch, 'require_can_send', paused)
    with pytest.raises(RuntimeError, match='paused'):
        begin(claimed)
    with sqlite3.connect(claimed[0]) as db:
        assert db.execute('SELECT count(*) FROM submission_intents').fetchone()[0] == 0


def test_database_refuses_requeue_after_dispatch(claimed):
    begin(claimed)
    with sqlite3.connect(claimed[0]) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE jobs SET status='pending'")


@pytest.mark.parametrize('dispatched', [False, True])
def test_crash_recovery_distinguishes_prepare_from_send(claimed, dispatched):
    if dispatched:
        begin(claimed)
    with sqlite3.connect(claimed[0]) as db:
        db.execute("UPDATE jobs SET lease_expires_at='2000-01-01T00:00:00+00:00'")
    assert dispatch.repair_expired(claimed[0]) == {'requeued':int(not dispatched), 'unknown':int(dispatched)}
    with sqlite3.connect(claimed[0]) as db:
        assert db.execute('SELECT status FROM jobs').fetchone()[0] == ('skip' if dispatched else 'pending')
        assert db.execute('SELECT finished_at FROM job_attempts').fetchone()[0]


def test_stale_worker_cannot_complete_new_claim(claimed):
    with sqlite3.connect(claimed[0]) as db:
        db.execute("UPDATE jobs SET last_fence_token='new-owner-token'")
        assert gate.guarded_mark(db,'j','worker','done','submitted',claimed[1]) == 0
    with pytest.raises(ValueError, match='stale fence'):
        finish(claimed)


def test_missing_receipt_is_never_success(claimed):
    begin(claimed)
    finish(claimed)
    with sqlite3.connect(claimed[0]) as db:
        assert db.execute('SELECT status,result FROM jobs').fetchone() == ('skip','unknown-outcome')
        assert db.execute('SELECT confirmed FROM application_runs').fetchone()[0] == 0


def test_wrong_receipt_identity_rolls_back(claimed):
    begin(claimed)
    with pytest.raises(ValueError, match='identity mismatch'):
        finish(claimed, url=URL+'wrong')
    with sqlite3.connect(claimed[0]) as db:
        assert db.execute('SELECT status FROM jobs').fetchone()[0] == 'claimed'
        assert db.execute('SELECT count(*) FROM applications').fetchone()[0] == 0


@pytest.mark.parametrize('tampered', [False, True])
def test_receipt_integrity_and_atomic_completion(claimed, tmp_path, tampered):
    begin(claimed)
    receipt = tmp_path / 'receipt.png'
    receipt.write_bytes(b'\x89PNG\r\n\x1a\n' + b'x'*100)
    proof = {'artifact':str(receipt), 'sha256':hashlib.sha256(receipt.read_bytes()).hexdigest()}
    with sqlite3.connect(claimed[0]) as db:
        db.execute("UPDATE submission_intents SET state='verified',evidence_json=?", (json.dumps(proof),))
    if tampered:
        receipt.write_bytes(b'changed')
        with pytest.raises(ValueError, match='integrity'):
            finish(claimed)
    else:
        finish(claimed)
    with sqlite3.connect(claimed[0]) as db:
        assert db.execute('SELECT status FROM jobs').fetchone()[0] == ('claimed' if tampered else 'done')
        assert db.execute('SELECT count(*) FROM applications').fetchone()[0] == int(not tampered)
        assert db.execute('SELECT confirmed FROM application_runs').fetchone()[0] == int(not tampered)


def test_click_timeout_keeps_uncertain_intent(claimed):
    page = SimpleNamespace(url=URL, inner_text=lambda _: 'Apply now')
    def timeout(**kwargs):
        raise TimeoutError('response lost after click')
    assert dispatch.terminal_click(page, SimpleNamespace(click=timeout), portal='linkedin') is False
    with sqlite3.connect(claimed[0]) as db:
        assert db.execute('SELECT state FROM submission_intents').fetchone()[0] == 'unknown'
    with pytest.raises(dispatch.DispatchBlocked):
        begin(claimed)


def test_changed_resume_blocks_before_click(claimed):
    from pathlib import Path
    Path(claimed[2].resume_path).write_bytes(b'replaced after approval')
    page = SimpleNamespace(url=URL, inner_text=lambda _: 'Apply now')
    with pytest.raises(dispatch.DispatchBlocked, match='resume bytes'):
        dispatch.terminal_click(page, None, portal='linkedin')
    with sqlite3.connect(claimed[0]) as db:
        assert db.execute('SELECT count(*) FROM submission_intents').fetchone()[0] == 0


def test_legacy_submission_blocks_duplicate(claimed):
    with sqlite3.connect(claimed[0]) as db:
        db.execute("INSERT INTO applications(portal,url,status,applied_at) VALUES('linkedin',?,'submitted','2026-01-01')", (URL,))
    with pytest.raises(dispatch.DispatchBlocked, match='history'):
        begin(claimed)
