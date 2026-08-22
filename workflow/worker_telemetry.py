from __future__ import annotations

import fcntl
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from .schema import migrate_queue

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL = re.compile(r"https?://\S+", re.I)
_TOKEN = re.compile(r"(?i)(?:token|cookie|bearer|password|secret)\s*[=:]\s*\S+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(value: object, limit: int = 240) -> str:
    text = str(value or "")
    text = _EMAIL.sub("[REDACTED_EMAIL]", text)
    text = _URL.sub("[REDACTED_URL]", text)
    text = _TOKEN.sub("[REDACTED_SECRET]", text)
    return text[:limit]


def _safe_code(value: object) -> str:
    raw = str(value or "").strip().lower()
    first = re.split(r"[|:]", raw, maxsplit=1)[0]
    match = re.match(r"[a-z0-9][a-z0-9_-]{0,79}", first)
    return match.group(0) if match else ""


class WorkerTelemetry:
    """Canonical SQLite worker state with private JSON/JSONL projections."""

    def __init__(self, db_path: str | Path, state_root: str | Path,
                 worker_id: str, adapter: str, *, unit: str | None = None):
        self.db_path = Path(db_path)
        self.worker_id = worker_id
        self.adapter = adapter
        self.unit = unit
        self.root = Path(state_root) / adapter / worker_id
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        migrate_queue(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=10)
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def _queue_depth(self) -> int:
        with self._connect() as db:
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if "jobs" not in tables:
                return 0
            if self.adapter == "review":
                prefixes = ("submit-unconfirmed%", "no-apply-modal%", "send-unconfirmed%",
                            "no-send-button%", "fill-err%", "unhandled-source%")
                where = " OR ".join(["result LIKE ?"] * len(prefixes))
                return int(db.execute(
                    f"SELECT COUNT(*) FROM jobs WHERE status='skip' AND ({where})",
                    prefixes,
                ).fetchone()[0])
            if self.worker_id.startswith("wf-r"):
                return int(db.execute(
                    """SELECT COUNT(*) FROM jobs WHERE portal='wellfound' AND status='skip'
                       AND result LIKE 'submit-unconfirmed%'"""
                ).fetchone()[0])
            portal = {
                "wellfound": "wellfound", "internshala": "internshala", "yc": "yc",
                "linkedin": "linkedin", "external": "external",
            }.get(self.adapter)
            if portal:
                return int(db.execute(
                    "SELECT COUNT(*) FROM jobs WHERE portal=? AND status='pending'", (portal,)
                ).fetchone()[0])
        return 0

    def _project(self, record: dict[str, object], status: dict[str, object], *,
                 emit_event: bool = True) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        lock_path = self.root / ".projection.lock"
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(lock_fd, "r+", encoding="utf-8") as lock_handle:
            os.chmod(lock_path, 0o600)
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            if emit_event:
                event_path = self.root / "events.jsonl"
                fd = os.open(event_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as handle:
                    os.chmod(event_path, 0o600)
                    handle.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            with self._connect() as db:
                current = db.execute(
                    """SELECT adapter,state,current_job_id,queue_depth,safe_detail,heartbeat_at
                       FROM worker_instances WHERE id=?""",
                    (self.worker_id,),
                ).fetchone()
            if current:
                status = {
                    "worker_id": self.worker_id,
                    "adapter": current[0],
                    "state": current[1],
                    "current_job_id": current[2],
                    "queue_depth": current[3],
                    "safe_detail": current[4] or "",
                    "updated_at": current[5],
                }
            target = self.root / "status.json"
            tmp = self.root / ".status.tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(status, handle, separators=(",", ":"), sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def _record(self, event: str, *, state: str, job_id: str | None = None,
                outcome_code: str | None = None, safe_detail: str = "",
                queue_depth: int | None = None, success: bool = False,
                active_job: bool = True) -> None:
        ts = _now()
        depth = self._queue_depth() if queue_depth is None else max(0, int(queue_depth))
        detail = _safe_code(safe_detail)
        safe_job = _safe(job_id, 100) if job_id else None
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT state,current_job_id,safe_detail FROM worker_instances WHERE id=?",
                (self.worker_id,),
            ).fetchone()
            emit_event = not (
                event in {"idle", "blocked"} and previous is not None
                and previous[0] == state and previous[1] is None
                and str(previous[2] or "") == detail
            )
            db.execute(
                """INSERT INTO worker_instances(
                     id,unit,adapter,state,current_run_id,heartbeat_at,last_success_at,
                     browser_pid,queue_depth,safe_detail,current_job_id,started_at,last_event_at
                   ) VALUES(?,?,?,?,NULL,?,?,NULL,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     unit=COALESCE(excluded.unit,worker_instances.unit),adapter=excluded.adapter,
                     state=excluded.state,heartbeat_at=excluded.heartbeat_at,
                     last_success_at=CASE WHEN ? THEN excluded.heartbeat_at ELSE worker_instances.last_success_at END,
                     queue_depth=excluded.queue_depth,safe_detail=excluded.safe_detail,
                     current_job_id=excluded.current_job_id,
                     started_at=COALESCE(worker_instances.started_at,excluded.started_at),
                     last_event_at=excluded.last_event_at""",
                (self.worker_id, self.unit, self.adapter, state, ts,
                 ts if success else None, depth, detail,
                 safe_job if active_job else None, ts, ts, int(success)),
            )
            if emit_event:
                db.execute(
                    """INSERT INTO worker_events(
                         worker_id,adapter,event,job_id,outcome_code,safe_detail,created_at
                       ) VALUES(?,?,?,?,?,?,?)""",
                    (self.worker_id, self.adapter, event, safe_job,
                         _safe_code(outcome_code) if outcome_code else None, detail, ts),
                )
        record: dict[str, object] = {
            "timestamp": ts, "worker_id": self.worker_id, "adapter": self.adapter,
            "event": event, "state": state,
        }
        if safe_job:
            record["job_id"] = safe_job
        if outcome_code:
            record["outcome_code"] = _safe_code(outcome_code)
        if detail:
            record["safe_detail"] = detail
        self._project(record, {
            "worker_id": self.worker_id, "adapter": self.adapter, "state": state,
            "current_job_id": safe_job if active_job else None,
            "queue_depth": depth,
            "safe_detail": detail, "updated_at": ts,
        }, emit_event=emit_event)

    def started(self) -> None:
        self._record("started", state="starting")

    def _stop_heartbeat(self) -> None:
        self._heartbeat_stop.set()
        thread = self._heartbeat_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1)
        self._heartbeat_thread = None

    def _heartbeat(self) -> None:
        while not self._heartbeat_stop.wait(30):
            ts = _now()
            depth = self._queue_depth()
            with self._connect() as db:
                db.execute(
                    "UPDATE worker_instances SET heartbeat_at=?,queue_depth=? WHERE id=? AND state='working'",
                    (ts, depth, self.worker_id),
                )
                row = db.execute(
                    "SELECT state,current_job_id,safe_detail FROM worker_instances WHERE id=?",
                    (self.worker_id,),
                ).fetchone()
            if row and row[0] == "working":
                self._project({}, {
                    "worker_id": self.worker_id, "adapter": self.adapter,
                    "state": row[0], "current_job_id": row[1],
                    "queue_depth": depth, "safe_detail": row[2] or "", "updated_at": ts,
                }, emit_event=False)

    def _start_heartbeat(self) -> None:
        self._stop_heartbeat()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat, name=f"telemetry-{self.worker_id}", daemon=True,
        )
        self._heartbeat_thread.start()

    def claimed(self, job_id: str, *, queue_depth: int | None = None) -> None:
        self._record("claimed", state="working", job_id=job_id, queue_depth=queue_depth)
        self._start_heartbeat()

    def outcome(self, job_id: str, outcome_code: str, safe_detail: str = "",
                *, queue_depth: int | None = None) -> None:
        self._stop_heartbeat()
        success = outcome_code in {"done", "sent", "submitted", "applied"}
        self._record("outcome", state="idle", job_id=job_id, outcome_code=outcome_code,
                     safe_detail=safe_detail, queue_depth=queue_depth, success=success,
                     active_job=False)

    def idle(self, *, queue_depth: int | None = None, safe_detail: str = "") -> None:
        self._stop_heartbeat()
        self._record("idle", state="idle", queue_depth=queue_depth, safe_detail=safe_detail)

    def blocked(self, safe_detail: str) -> None:
        self._stop_heartbeat()
        self._record("blocked", state="blocked", safe_detail=safe_detail)


@lru_cache(maxsize=64)
def telemetry_for(worker_id: str, adapter: str, db_path: str,
                  state_root: str, unit: str | None = None) -> WorkerTelemetry:
    return WorkerTelemetry(db_path, state_root, worker_id, adapter, unit=unit)
