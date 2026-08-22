from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from cryptography.fernet import Fernet


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OperatorTaskRepository:
    def __init__(self, path: str | Path, fernet: Fernet):
        self.path = str(path)
        self.fernet = fernet

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def create(
        self,
        task_type: str,
        safe_summary: str,
        *,
        run_id: str | None = None,
        site_id: int | None = None,
    ) -> str:
        allowed = {
            "unknown_question",
            "login_required",
            "recipe_drift",
            "ambiguous_confirmation",
            "manual_review",
        }
        if task_type not in allowed:
            raise ValueError("invalid operator task type")
        if not safe_summary.strip():
            raise ValueError("safe summary is required")
        task_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute(
                """INSERT INTO operator_tasks
                (id,run_id,site_id,type,status,safe_summary,created_at)
                VALUES(?,?,?,?,'open',?,?)""",
                (task_id, run_id, site_id, task_type, safe_summary.strip(), _now()),
            )
        return task_id

    def list_open(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT id,run_id,site_id,type,status,safe_summary,created_at
                FROM operator_tasks WHERE status='open' ORDER BY created_at,id"""
            ).fetchall()
            return [dict(row) for row in rows]

    def resolve(
        self,
        task_id: str,
        *,
        decision_type: str,
        safe_summary: str,
        payload: Mapping[str, Any] | None,
        actor: str,
    ) -> None:
        encrypted = None
        if payload is not None:
            encrypted = self.fernet.encrypt(
                json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode()
            )
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status FROM operator_tasks WHERE id=?", (task_id,)
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            if row["status"] != "open":
                raise ValueError("operator task is already closed")
            db.execute(
                "UPDATE operator_tasks SET status='resolved',resolved_at=? WHERE id=?",
                (_now(), task_id),
            )
            db.execute(
                """INSERT INTO operator_decisions
                (task_id,decision_type,safe_summary,payload_enc,actor,created_at)
                VALUES(?,?,?,?,?,?)""",
                (
                    task_id,
                    decision_type,
                    safe_summary.strip(),
                    encrypted,
                    actor,
                    _now(),
                ),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
