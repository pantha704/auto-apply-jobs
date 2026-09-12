"""Durable terminal-action boundary: uncertain sends are never retried automatically."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .farm_control import require_can_send


class DispatchBlocked(RuntimeError):
    pass


def application_key(portal, url):
    from job_identity import canonical_url
    return hashlib.sha256(f"{portal}:{canonical_url(url)}".encode()).hexdigest()


def begin_dispatch(database, job_id, worker_id, fence_token, *, portal, url, validate_release=None):
    from .application_gate import check_fence
    require_can_send(portal)
    if validate_release:
        validate_release()
    with sqlite3.connect(database, timeout=10) as db:
        db.execute('BEGIN IMMEDIATE')
        require_can_send(portal)
        if not check_fence(db, job_id, worker_id, fence_token):
            raise DispatchBlocked('claim expired or no longer owned')
        row = db.execute('SELECT portal,url FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row or row[0]!=portal or application_key(portal,row[1])!=application_key(portal,url):
            raise DispatchBlocked('application identity changed')
        for previous_url,status in db.execute('SELECT url,status FROM applications WHERE portal=?', (portal,)):
            if application_key(portal,previous_url)==application_key(portal,url) and any(marker in (status or '').lower() for marker in ('submitted','applied','unconfirmed','unknown-outcome')):
                raise DispatchBlocked('application history requires reconciliation')
        identifier = uuid.uuid4().hex
        try:
            db.execute("""INSERT INTO submission_intents(id,job_id,application_key,worker_id,fence_token,created_at,state)
                VALUES(?,?,?,?,?,?,'unknown')""", (identifier,job_id,application_key(portal,url),worker_id,fence_token,datetime.now(timezone.utc).isoformat()))
        except sqlite3.IntegrityError as exc:
            raise DispatchBlocked('application already dispatched; reconciliation required') from exc
        db.execute("UPDATE jobs SET last_outcome_code='submission_started' WHERE id=?", (job_id,))
    return identifier


def terminal_click(page, element, *, portal, timeout_ms=5000):
    from .application_gate import active_claim, published_runtime
    from submission_signals import has_submission_confirmation
    entry = active_claim()
    if not entry or not entry.get('bundle'):
        raise DispatchBlocked('terminal action requires an approved active claim')
    bundle = entry['bundle']
    def validate():
        session_portal = 'himalayas' if entry['portal']=='external' else entry['portal']
        current = published_runtime(session_portal if bundle.session_revision else None, require_session=bool(bundle.session_revision))
        if not bundle.matches_runtime(current):
            raise DispatchBlocked('approved release changed')
        if not bundle.resume_sha256 or bundle.resume_sha256!=hashlib.sha256(Path(bundle.resume_path).read_bytes()).hexdigest():
            raise DispatchBlocked('approved resume bytes changed')
    if has_submission_confirmation(page.inner_text('body')):
        raise DispatchBlocked('confirmation already present before dispatch')
    # Redirecting adapters require explicit certification of the requisition mapping.
    if application_key(entry['portal'],page.url)!=application_key(entry['portal'],entry['url']):
        raise DispatchBlocked('browser left the claimed job URL')
    begin_dispatch(entry['database'],entry['job_id'],entry['worker_id'],entry['fence_token'],portal=entry['portal'],url=entry['url'],validate_release=validate)
    try:
        element.click(timeout=timeout_ms)
        for _ in range(8):
            page.wait_for_timeout(500)
            if observe_confirmation(page,entry):
                break
        return True
    except Exception:
        return False


def observe_confirmation(page, entry=None):
    from .application_gate import active_claim
    from submission_signals import has_submission_confirmation
    entry = entry or active_claim()
    if not entry:
        return False
    try:
        if not has_submission_confirmation(page.inner_text('body')):
            return False
        import audit
        snap = audit.snapshot(page,entry['portal'],entry['job_id'],'receipt')
        path = snap.get('png') if snap else None
        if not path or not Path(path).is_file():
            return False
        proof = {'kind':'ui_transition','artifact':path,'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest()}
        with sqlite3.connect(entry['database'], timeout=10) as db:
            cur = db.execute("UPDATE submission_intents SET state='verified',evidence_json=? WHERE job_id=? AND fence_token=? AND state='unknown'", (json.dumps(proof),entry['job_id'],entry['fence_token']))
            return cur.rowcount==1
    except Exception:
        return False


def repair_expired(database, *, worker_id=None, portal=None, reason='lease expired'):
    now = datetime.now(timezone.utc).isoformat()
    result = {'requeued':0,'unknown':0}
    with sqlite3.connect(database, timeout=10) as db:
        db.execute('BEGIN IMMEDIATE')
        where = "status='claimed' AND (lease_expires_at IS NULL OR julianday(lease_expires_at)<=julianday(?))"
        args = [now]
        if worker_id:
            where+=' AND claimed_by=?'; args.append(worker_id)
        if portal:
            where+=' AND portal=?'; args.append(portal)
        for jid,previous,outcome in db.execute(f'SELECT id,result,last_outcome_code FROM jobs WHERE {where}', args).fetchall():
            intent = db.execute('SELECT 1 FROM submission_intents WHERE job_id=?', (jid,)).fetchone()
            uncertain = bool(intent) or any(x in f'{previous} {outcome}' for x in ('submit','send-unconfirmed','applied','confirmation_ambiguous','unknown-outcome'))
            status,code = ('skip','unknown-outcome') if uncertain else ('pending','lease-expired')
            db.execute('UPDATE jobs SET status=?,result=?,last_outcome_code=?,claimed_by=NULL,lease_expires_at=NULL,admin_reset_at=?,admin_reset_reason=? WHERE id=?', (status,code,code,now,reason,jid))
            db.execute("UPDATE application_runs SET state='finished',finished_at=?,outcome_code=? WHERE job_id=? AND state='running'", (now,code,jid))
            db.execute('UPDATE job_attempts SET finished_at=?,outcome_code=? WHERE run_id IN (SELECT id FROM application_runs WHERE job_id=?) AND finished_at IS NULL', (now,code,jid))
            result['unknown' if uncertain else 'requeued']+=1
    return result
