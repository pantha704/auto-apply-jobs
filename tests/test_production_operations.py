"""Operational regressions: approval snapshots, session fencing and fresh setup."""
import sqlite3
import pytest
from datetime import timedelta

from test_portal_session_manager import manager, publish  # noqa: F401
from test_cold_email_sender import _schema
from test_analytics import _db, NOW
from workflow.cold_email import ColdEmailQueue
from workflow.analytics import aggregate_analytics


def test_email_recipient_is_frozen_at_approval(tmp_path):
    path = tmp_path / 'control.db'
    _schema(path)
    queue = ColdEmailQueue(path)
    queue.approve('c1', approved_by='operator')
    with sqlite3.connect(path) as db:
        db.execute("UPDATE cold_contacts SET email='different@example.test'")
    claim = queue.claim('worker')
    assert claim.to == 'jobs@acme.test'


def test_old_email_attempt_cannot_finish_reclaimed_send(tmp_path):
    path = tmp_path / 'control.db'
    _schema(path)
    queue = ColdEmailQueue(path)
    queue.approve('c1', approved_by='operator')
    old = queue.claim('worker')
    queue.failed(old, 'not-sent', retryable=True, retry_after=timedelta(0))
    new = queue.claim('worker')
    assert new.attempt_token != old.attempt_token
    with pytest.raises(RuntimeError, match='no longer owned'):
        queue.sent(old, provider_id='stale-response')
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT status,provider_id FROM cold_email_sends').fetchone() == ('sending', None)


def test_session_renewal_fence_never_restarts_after_release(manager):
    service, _, _, _ = manager
    first = service.acquire_renewal('linkedin', 'worker')
    service.release_renewal('linkedin', first.token)
    second = service.acquire_renewal('linkedin', 'worker')
    assert second.fencing_token > first.fencing_token


def test_delayed_probe_cannot_invalidate_new_session(manager):
    service, _, _, _ = manager
    old = publish(service, 'linkedin')
    new = publish(service, 'linkedin', 'new-cookie')
    service.record_health('linkedin', 'expired', version_id=old.id)
    status = service.public_status('linkedin')
    assert status['state'] == 'valid'
    assert status['current_revision'] == new.revision


def test_migration_does_not_hide_historical_confirmations(tmp_path):
    path = tmp_path / 'queue.db'
    with _db(path) as db:
        db.execute("INSERT INTO applications VALUES(1,'linkedin','Acme','Role','https://example.test/job','2026-08-18T10:00:00+00:00','submitted')")
    result = aggregate_analytics(path, '7d', now=NOW)
    assert result['confirmed_applications'] == 1
    assert result['portals'] == {'linkedin': 1}
    assert result['run_count'] == 0


def test_bootstrap_is_paused_and_preserves_existing_data(tmp_path, monkeypatch):
    from workflow.bootstrap import initialize_state
    from controlplane.app import settings
    settings.cache_clear()
    for env, name in [('JOBHUNT_QUEUE_DB','queue.db'),('JOBHUNT_CONTROL_DB','control.db'),
                      ('JOBHUNT_VAULT_KEY','key'),('JOBHUNT_RESUME_STORAGE','resumes'),
                      ('JOBHUNT_SESSION_STORAGE','sessions'),('JOBHUNT_PAUSE_FLAG','paused')]:
        monkeypatch.setenv(env,str(tmp_path/name))
    monkeypatch.setenv('JOBHUNT_SITE_BOOTSTRAP','0')
    monkeypatch.setenv('JOBHUNT_PROFILE_BOOTSTRAP','0')
    assert initialize_state()['fresh_installation'] is True
    assert (tmp_path/'paused').is_file()
    key = (tmp_path/'key').read_bytes()
    with sqlite3.connect(tmp_path/'queue.db') as db:
        db.execute("INSERT INTO jobs(id,portal,url,status) VALUES('kept','linkedin','https://example.test/job','pending')")
    assert initialize_state()['fresh_installation'] is False
    assert (tmp_path/'key').read_bytes() == key
    with sqlite3.connect(tmp_path/'queue.db') as db:
        assert db.execute('SELECT id FROM jobs').fetchone()[0] == 'kept'


def test_bootstrap_refuses_to_replace_lost_vault_key(tmp_path, monkeypatch):
    import pytest
    from workflow.bootstrap import initialize_state
    from controlplane.app import settings
    settings.cache_clear()
    control = tmp_path/'control.db'
    control.write_bytes(b'existing database')
    monkeypatch.setenv('JOBHUNT_CONTROL_DB',str(control))
    monkeypatch.setenv('JOBHUNT_QUEUE_DB',str(tmp_path/'queue.db'))
    monkeypatch.setenv('JOBHUNT_VAULT_KEY',str(tmp_path/'missing.key'))
    with pytest.raises(RuntimeError, match='Restore its original key'):
        initialize_state()
