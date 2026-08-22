from __future__ import annotations

import json
import secrets
import sqlite3
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Claim, Outcome, OutcomeCode, SubmissionEvidence


class LeaseConflict(RuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


class WorkflowStore:
    def __init__(
        self,
        path: str | Path,
        *,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.path = Path(path)
        self.now = now

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def claim_next(
        self,
        worker_id: str,
        *,
        portal: str | None = None,
        lease_seconds: int = 300,
    ) -> Claim | None:
        current = self.now()
        current_iso = _iso(current)
        expires = _iso(current + timedelta(seconds=lease_seconds))
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            where = "(status='pending' OR (status='claimed' AND lease_expires_at < ?))"
            params: list[object] = [current_iso]
            if portal:
                where += " AND portal=?"
                params.append(portal)
            row = db.execute(
                f"SELECT id,portal,url,title,status,attempt_count FROM jobs WHERE {where} "
                "AND (next_attempt_at IS NULL OR next_attempt_at <= ?) "
                "ORDER BY prio DESC,rowid LIMIT 1",
                (*params, current_iso),
            ).fetchone()
            if row is None:
                db.rollback()
                return None
            if row["status"] == "claimed":
                db.execute(
                    """UPDATE job_attempts SET finished_at=?,outcome_code='lease_expired',
                    retryable=1,safe_detail='claim lease expired'
                    WHERE run_id IN (
                      SELECT id FROM application_runs
                      WHERE job_id=? AND state='running'
                    )""",
                    (current_iso, row["id"]),
                )
                db.execute(
                    """UPDATE application_runs SET state='failed',finished_at=?,
                    outcome_code='lease_expired',safe_detail='claim lease expired'
                    WHERE job_id=? AND state='running'""",
                    (current_iso, row["id"]),
                )
            updated = db.execute(
                """UPDATE jobs SET status='claimed',claimed_by=?,claimed_at=?,
                lease_expires_at=?,attempt_count=attempt_count+1
                WHERE id=? AND (status='pending' OR
                  (status='claimed' AND lease_expires_at < ?))""",
                (worker_id, current_iso, expires, row["id"], current_iso),
            )
            if updated.rowcount != 1:
                db.rollback()
                return None
            attempt_count = int(row["attempt_count"] or 0) + 1
            run_id = str(uuid.uuid4())
            lease_token = secrets.token_urlsafe(32)
            db.execute(
                """INSERT INTO application_runs
                (id,job_id,adapter,lease_token,worker_id,state,started_at,confirmed)
                VALUES(?,?,?,?,?,?,?,0)""",
                (
                    run_id,
                    row["id"],
                    row["portal"] or "generic",
                    lease_token,
                    worker_id,
                    "running",
                    current_iso,
                ),
            )
            db.execute(
                """INSERT INTO job_attempts(run_id,attempt_no,started_at,retryable)
                VALUES(?,?,?,0)""",
                (run_id, attempt_count, current_iso),
            )
            db.commit()
            return Claim(
                job_id=row["id"],
                run_id=run_id,
                worker_id=worker_id,
                portal=row["portal"] or "generic",
                url=row["url"],
                title=row["title"] or "",
                attempt_count=attempt_count,
                lease_token=lease_token,
                lease_expires_at=expires,
            )
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def heartbeat(
        self,
        run_id: str,
        worker_id: str,
        lease_token: str,
        *,
        lease_seconds: int = 300,
    ) -> str:
        current = self.now()
        expires = _iso(current + timedelta(seconds=lease_seconds))
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT job_id,worker_id,lease_token,state FROM application_runs WHERE id=?",
                (run_id,),
            ).fetchone()
            if (
                row is None
                or row["worker_id"] != worker_id
                or not secrets.compare_digest(row["lease_token"], lease_token)
                or row["state"] != "running"
            ):
                raise LeaseConflict("run is not owned by worker")
            updated = db.execute(
                """UPDATE jobs SET lease_expires_at=?
                WHERE id=? AND status='claimed' AND claimed_by=?
                AND lease_expires_at>=?""",
                (expires, row["job_id"], worker_id, _iso(current)),
            )
            if updated.rowcount != 1:
                raise LeaseConflict("job lease is not owned by worker")
            db.commit()
            return expires
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def finish(
        self, run_id: str, worker_id: str, lease_token: str, outcome: Outcome
    ) -> None:
        if outcome.code is OutcomeCode.SUBMITTED:
            raise ValueError("submitted outcomes require confirm_submission()")
        finished_at = _iso(self.now())
        legacy_status, legacy_result = _legacy(outcome)
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT job_id,worker_id,lease_token,state FROM application_runs WHERE id=?",
                (run_id,),
            ).fetchone()
            if (
                row is None
                or row["worker_id"] != worker_id
                or not secrets.compare_digest(row["lease_token"], lease_token)
                or row["state"] != "running"
            ):
                raise LeaseConflict("run is not owned by worker")
            updated = db.execute(
                """UPDATE jobs SET status=?,result=?,last_outcome_code=?,
                claimed_by=NULL,claimed_at=NULL,lease_expires_at=NULL
                WHERE id=? AND status='claimed' AND claimed_by=?
                AND lease_expires_at>=?""",
                (
                    legacy_status,
                    legacy_result,
                    outcome.code.value,
                    row["job_id"],
                    worker_id,
                    finished_at,
                ),
            )
            if updated.rowcount != 1:
                raise LeaseConflict("job lease is not owned by worker")
            db.execute(
                """UPDATE application_runs SET state='finished',finished_at=?,
                confirmed=?,outcome_code=?,safe_detail=? WHERE id=?""",
                (
                    finished_at,
                    int(outcome.confirmed),
                    outcome.code.value,
                    outcome.safe_detail,
                    run_id,
                ),
            )
            db.execute(
                """UPDATE job_attempts SET finished_at=?,outcome_code=?,
                retryable=?,safe_detail=? WHERE run_id=?""",
                (
                    finished_at,
                    outcome.code.value,
                    int(outcome.retryable),
                    outcome.safe_detail,
                    run_id,
                ),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def confirm_submission(
        self,
        run_id: str,
        worker_id: str,
        lease_token: str,
        *,
        portal: str,
        company: str,
        role: str,
        url: str,
        evidence: SubmissionEvidence,
    ) -> None:
        """Atomically confirm one submission and dual-write the legacy audit row."""
        finished_at = _iso(self.now())
        evidence_json = json.dumps(
            {
                "observed_at": evidence.observed_at,
                "success_text": evidence.success_text,
                "application_id": evidence.application_id,
                "artifact_ids": list(evidence.artifact_ids),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT job_id,worker_id,lease_token,state FROM application_runs WHERE id=?",
                (run_id,),
            ).fetchone()
            if (
                row is None
                or row["worker_id"] != worker_id
                or not secrets.compare_digest(row["lease_token"], lease_token)
                or row["state"] != "running"
            ):
                raise LeaseConflict("run is not owned by worker")
            if evidence.artifact_ids:
                marks = ",".join("?" for _ in evidence.artifact_ids)
                count = db.execute(
                    f"SELECT COUNT(*) FROM artifacts WHERE run_id=? AND id IN ({marks})",
                    (run_id, *evidence.artifact_ids),
                ).fetchone()[0]
                if count != len(set(evidence.artifact_ids)):
                    raise ValueError("submission evidence references foreign artifacts")
            updated = db.execute(
                """UPDATE jobs SET status='done',result='submitted',
                last_outcome_code='submitted',claimed_by=NULL,claimed_at=NULL,
                lease_expires_at=NULL
                WHERE id=? AND status='claimed' AND claimed_by=?
                AND lease_expires_at>=?""",
                (row["job_id"], worker_id, finished_at),
            )
            if updated.rowcount != 1:
                raise LeaseConflict("job lease is not owned by worker")
            db.execute(
                """UPDATE application_runs SET state='submitted',finished_at=?,
                confirmed=1,outcome_code='submitted',safe_detail=?,
                submission_evidence_json=? WHERE id=?""",
                (finished_at, "verified portal confirmation", evidence_json, run_id),
            )
            db.execute(
                """UPDATE job_attempts SET finished_at=?,outcome_code='submitted',
                retryable=0,safe_detail='verified portal confirmation' WHERE run_id=?""",
                (finished_at, run_id),
            )
            existing = db.execute(
                """SELECT id FROM applications
                WHERE portal=? AND url=? AND status='submitted' LIMIT 1""",
                (portal, url),
            ).fetchone()
            if existing is None:
                db.execute(
                    """INSERT INTO applications
                    (portal,company,role,status,applied_at,url)
                    VALUES(?,?,?,?,?,?)""",
                    (
                        portal,
                        company,
                        role,
                        "submitted",
                        finished_at,
                        url,
                    ),
                )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def _legacy(outcome: Outcome) -> tuple[str, str]:
    if outcome.code is OutcomeCode.SUBMITTED:
        return "done", "submitted"
    if outcome.code is OutcomeCode.ALREADY_APPLIED:
        return "done", "already-applied"
    if outcome.retryable:
        return "pending", outcome.code.value
    return "skip", outcome.code.value
