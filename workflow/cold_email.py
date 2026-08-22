from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Protocol


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class EmailClaim:
    id: str
    contact_id: str
    to: str
    subject: str
    body: str
    worker_id: str


class ColdEmailQueue:
    """Transactional cold-email queue; SQLite is the canonical state."""

    def __init__(self, path: str | Path, *, now: Callable[[], datetime] = _utc_now):
        self.path = Path(path)
        self.now = now

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def approve(self, contact_id: str, *, approved_by: str) -> str:
        ts = _iso(self.now())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                """SELECT id FROM cold_email_sends WHERE contact_id=?
                   AND status IN ('queued','sending','sent') ORDER BY created_at DESC LIMIT 1""",
                (contact_id,),
            ).fetchone()
            if existing:
                return str(existing["id"])
            contact = db.execute("SELECT * FROM cold_contacts WHERE id=?", (contact_id,)).fetchone()
            if not contact:
                raise KeyError(contact_id)
            if contact["status"] != "drafted" or not contact["draft_subject"] or not contact["draft_body"]:
                raise ValueError("approved send requires a drafted contact")
            send_id = str(uuid.uuid4())
            db.execute(
                """INSERT INTO cold_email_sends(
                     id,contact_id,template_id,subject,status,created_at,body,
                     approved_at,approved_by,attempt_count,updated_at
                   ) VALUES(?,?,?,?, 'queued',?,?,?,?,0,?)""",
                (send_id, contact_id, contact["template_id"], contact["draft_subject"],
                 ts, contact["draft_body"], ts, approved_by, ts),
            )
            return send_id

    def has_ready(self) -> bool:
        ts = _iso(self.now())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE cold_email_sends SET status='unknown',
                     error='delivery_confirmation_unknown',updated_at=?
                   WHERE status='sending' AND lease_expires_at<?""",
                (ts, ts),
            )
            return db.execute(
                """SELECT 1 FROM cold_email_sends
                   WHERE status='queued' AND approved_at IS NOT NULL
                     AND (next_attempt_at IS NULL OR next_attempt_at<=?) LIMIT 1""",
                (ts,),
            ).fetchone() is not None

    def claim(self, worker_id: str, *, lease_seconds: int = 300) -> EmailClaim | None:
        now = self.now()
        ts, expires = _iso(now), _iso(now + timedelta(seconds=lease_seconds))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE cold_email_sends SET status='unknown',
                     error='delivery_confirmation_unknown',updated_at=?
                   WHERE status='sending' AND lease_expires_at<?""",
                (ts, ts),
            )
            row = db.execute(
                """SELECT s.id,s.contact_id,s.subject,s.body,c.email
                   FROM cold_email_sends s JOIN cold_contacts c ON c.id=s.contact_id
                   WHERE s.status='queued'
                     AND s.approved_at IS NOT NULL
                     AND (s.next_attempt_at IS NULL OR s.next_attempt_at<=?)
                   ORDER BY s.created_at LIMIT 1""",
                (ts,),
            ).fetchone()
            if not row:
                return None
            updated = db.execute(
                """UPDATE cold_email_sends SET status='sending',claimed_by=?,claimed_at=?,
                     lease_expires_at=?,attempt_count=attempt_count+1,updated_at=?
                   WHERE id=? AND (status='queued' OR (status='sending' AND lease_expires_at<?))""",
                (worker_id, ts, expires, ts, row["id"], ts),
            )
            if updated.rowcount != 1:
                return None
            return EmailClaim(str(row["id"]), str(row["contact_id"]), str(row["email"]),
                              str(row["subject"]), str(row["body"]), worker_id)

    def sent(self, claim: EmailClaim, *, provider_id: str) -> None:
        ts = _iso(self.now())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            updated = db.execute(
                """UPDATE cold_email_sends SET status='sent',provider_id=?,sent_at=?,updated_at=?,
                     lease_expires_at=NULL,error=NULL WHERE id=? AND status='sending' AND claimed_by=?""",
                (provider_id, ts, ts, claim.id, claim.worker_id),
            )
            if updated.rowcount != 1:
                raise RuntimeError("email claim is no longer owned")
            db.execute(
                "UPDATE cold_contacts SET status='sent',last_sent_at=?,updated_at=? WHERE id=?",
                (ts, ts, claim.contact_id),
            )

    def failed(self, claim: EmailClaim, error: str, *, retryable: bool,
               retry_after: timedelta = timedelta(minutes=5)) -> None:
        ts = _iso(self.now())
        status = "queued" if retryable else "failed"
        next_attempt = _iso(self.now() + retry_after) if retryable else None
        with self._connect() as db:
            updated = db.execute(
                """UPDATE cold_email_sends SET status=?,error=?,next_attempt_at=?,updated_at=?,
                     lease_expires_at=NULL WHERE id=? AND status='sending' AND claimed_by=?""",
                (status, error[:300], next_attempt, ts, claim.id, claim.worker_id),
            )
            if updated.rowcount != 1:
                raise RuntimeError("email claim is no longer owned")
            if not retryable:
                db.execute("UPDATE cold_contacts SET status='failed',updated_at=? WHERE id=?",
                           (ts, claim.contact_id))

    def unknown(self, claim: EmailClaim, error: str) -> None:
        ts = _iso(self.now())
        with self._connect() as db:
            updated = db.execute(
                """UPDATE cold_email_sends SET status='unknown',error=?,updated_at=?,
                     lease_expires_at=NULL WHERE id=? AND status='sending' AND claimed_by=?""",
                (error[:300], ts, claim.id, claim.worker_id),
            )
            if updated.rowcount != 1:
                raise RuntimeError("email claim is no longer owned")


class EventJournal:
    """Append-only privacy-safe worker projection; never the source of truth."""

    ALLOWED = {"send_id", "contact_id", "provider_id", "error_code", "attempt"}

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, event: str, **fields: object) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        record: dict[str, object] = {"timestamp": _iso(_utc_now()), "event": event}
        record.update({key: value for key, value in fields.items() if key in self.ALLOWED})
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        fd = os.open(self.path, flags, 0o600)
        try:
            os.chmod(self.path, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                handle.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise


class EmailProvider(Protocol):
    def is_ready(self) -> bool: ...
    def send(self, *, to: str, subject: str, body: str) -> str: ...


class ColdEmailSender:
    def __init__(self, queue: ColdEmailQueue, provider: EmailProvider,
                 journal: EventJournal, worker_id: str):
        self.queue = queue
        self.provider = provider
        self.journal = journal
        self.worker_id = worker_id

    def run_once(self) -> str:
        if not self.queue.has_ready():
            return "queue_empty"
        if not self.provider.is_ready():
            return "provider_not_authenticated"
        claim = self.queue.claim(self.worker_id)
        if claim is None:
            return "queue_empty"
        self.journal.append("claimed", send_id=claim.id, contact_id=claim.contact_id)
        try:
            provider_id = self.provider.send(to=claim.to, subject=claim.subject, body=claim.body)
        except Exception as exc:
            code = type(exc).__name__.lower()
            self.queue.unknown(claim, code)
            self.journal.append("confirmation_unknown", send_id=claim.id,
                                contact_id=claim.contact_id, error_code=code)
            return "confirmation_unknown"
        self.queue.sent(claim, provider_id=provider_id)
        self.journal.append("sent", send_id=claim.id, contact_id=claim.contact_id,
                            provider_id=provider_id)
        return "sent"
