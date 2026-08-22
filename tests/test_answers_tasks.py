from __future__ import annotations

import sqlite3

from cryptography.fernet import Fernet

from workflow.answers import AnswerRepository
from workflow.schema import migrate_control
from workflow.tasks import OperatorTaskRepository


def control_fixture(path):
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE sites (
          id INTEGER PRIMARY KEY, name TEXT, base_url TEXT,
          login_url TEXT, adapter TEXT, username_enc BLOB,
          password_enc BLOB, session_path TEXT, enabled INTEGER,
          last_check TEXT, last_error TEXT
        );
        CREATE TABLE profile_fields (
          field TEXT PRIMARY KEY, value_enc BLOB, updated_at TEXT
        );
        """
    )
    db.close()
    migrate_control(path)


def test_answer_versions_are_encrypted_scoped_and_metadata_only(tmp_path):
    path = tmp_path / "control.db"
    control_fixture(path)
    key = Fernet.generate_key()
    repo = AnswerRepository(path, Fernet(key))

    first = repo.create_draft(
        "work_authorization",
        "Yes",
        answer_type="boolean",
        scope={"country": "IN"},
        provenance="user",
    )
    assert repo.lookup("work_authorization", {"country": "IN"}) is None
    repo.approve(first)
    assert repo.lookup("work_authorization", {"country": "IN"}) == "Yes"
    assert repo.lookup("work_authorization", {"country": "US"}) is None

    metadata = repo.list_metadata()
    assert metadata[0]["question_key"] == "work_authorization"
    assert "answer" not in metadata[0]
    assert "Yes" not in path.read_bytes().decode("latin1")

    second = repo.create_draft(
        "work_authorization",
        "No",
        answer_type="boolean",
        scope={"country": "IN"},
        provenance="user",
    )
    repo.approve(second)
    assert repo.lookup("work_authorization", {"country": "IN"}) == "No"
    statuses = {row["id"]: row["status"] for row in repo.list_metadata()}
    assert statuses[first] == "retired"
    assert statuses[second] == "approved"


def test_unknown_question_creates_operator_task_without_answer(tmp_path):
    path = tmp_path / "control.db"
    control_fixture(path)
    fernet = Fernet(Fernet.generate_key())
    answers = AnswerRepository(path, fernet)
    tasks = OperatorTaskRepository(path, fernet)

    value = answers.resolve_or_open_task(
        question_key="security_clearance",
        context={"country": "IN"},
        tasks=tasks,
        run_id="run-1",
        site_id=None,
    )
    assert value is None
    open_tasks = tasks.list_open()
    assert len(open_tasks) == 1
    assert open_tasks[0]["type"] == "unknown_question"
    assert "answer" not in open_tasks[0]

    tasks.resolve(
        open_tasks[0]["id"],
        decision_type="answer_later",
        safe_summary="Candidate review recorded",
        payload={"question_key": "security_clearance"},
        actor="candidate",
    )
    assert tasks.list_open() == []
