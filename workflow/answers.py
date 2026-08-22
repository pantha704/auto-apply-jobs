from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

from cryptography.fernet import Fernet


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_scope(scope: Mapping[str, Any]) -> str:
    return json.dumps(dict(scope), sort_keys=True, separators=(",", ":"))


class TaskSink(Protocol):
    def create(
        self,
        task_type: str,
        safe_summary: str,
        *,
        run_id: str | None = None,
        site_id: int | None = None,
    ) -> str: ...


class AnswerRepository:
    def __init__(self, path: str | Path, fernet: Fernet):
        self.path = str(path)
        self.fernet = fernet

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def create_draft(
        self,
        question_key: str,
        answer: Any,
        *,
        answer_type: str,
        scope: Mapping[str, Any],
        provenance: str,
    ) -> str:
        question_key = question_key.strip().casefold()
        if not question_key or not answer_type or not provenance:
            raise ValueError("question key, answer type, and provenance are required")
        identifier = uuid.uuid4().hex
        encrypted = self.fernet.encrypt(
            json.dumps(answer, ensure_ascii=False, separators=(",", ":")).encode()
        )
        scope_json = _canonical_scope(scope)
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM answer_entries WHERE question_key=?",
                (question_key,),
            ).fetchone()[0]
            db.execute(
                """INSERT INTO answer_entries
                (id,question_key,answer_enc,answer_type,scope_json,version,status,
                 provenance,created_at)
                VALUES(?,?,?,?,?,?,'draft',?,?)""",
                (
                    identifier,
                    question_key,
                    encrypted,
                    answer_type,
                    scope_json,
                    version,
                    provenance,
                    _now(),
                ),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return identifier

    def approve(self, answer_id: str) -> None:
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT question_key,scope_json,status FROM answer_entries WHERE id=?",
                (answer_id,),
            ).fetchone()
            if row is None:
                raise KeyError(answer_id)
            if row["status"] != "draft":
                raise ValueError("only draft answers can be approved")
            db.execute(
                """UPDATE answer_entries SET status='retired'
                WHERE question_key=? AND scope_json=? AND status='approved'""",
                (row["question_key"], row["scope_json"]),
            )
            db.execute(
                "UPDATE answer_entries SET status='approved',approved_at=? WHERE id=?",
                (_now(), answer_id),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def lookup(self, question_key: str, context: Mapping[str, Any]) -> Any | None:
        db = self._connect()
        try:
            rows = db.execute(
                """SELECT answer_enc,scope_json FROM answer_entries
                WHERE question_key=? AND status='approved' ORDER BY version DESC""",
                (question_key.strip().casefold(),),
            ).fetchall()
        finally:
            db.close()
        for row in rows:
            scope = json.loads(row["scope_json"])
            if all(value == "*" or context.get(key) == value for key, value in scope.items()):
                return json.loads(self.fernet.decrypt(row["answer_enc"]).decode())
        return None

    def list_metadata(self) -> list[dict[str, Any]]:
        db = self._connect()
        try:
            rows = db.execute(
                """SELECT id,question_key,answer_type,scope_json,version,status,
                provenance,created_at,approved_at FROM answer_entries
                ORDER BY question_key,version DESC"""
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            db.close()

    def resolve_or_open_task(
        self,
        *,
        question_key: str,
        context: Mapping[str, Any],
        tasks: TaskSink,
        run_id: str | None,
        site_id: int | None,
    ) -> Any | None:
        answer = self.lookup(question_key, context)
        if answer is not None:
            return answer
        tasks.create(
            "unknown_question",
            f"Candidate answer required: {question_key.strip().casefold()}",
            run_id=run_id,
            site_id=site_id,
        )
        return None
