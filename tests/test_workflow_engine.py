from __future__ import annotations

import sqlite3

import pytest

from workflow.engine import Event, InvalidTransition, State, StateMachine
from workflow.recipe import RecipeCompiler, RecoveryAction
from workflow.schema import migrate_queue
from workflow.verifier import Observation, SubmissionVerifier


def test_state_machine_is_typed_and_fail_closed():
    machine = StateMachine()
    assert machine.state is State.INIT
    machine.apply(Event.PROFILE_APPROVED)
    machine.apply(Event.SESSION_ACQUIRED)
    machine.apply(Event.JOB_OPENED)
    machine.apply(Event.FORM_DISCOVERED)
    machine.apply(Event.FORM_COMPLETED)
    assert machine.state is State.SUBMIT_READY

    with pytest.raises(InvalidTransition):
        machine.apply(Event.CONFIRMATION_OBSERVED)

    machine.apply(Event.SUBMIT_AUTHORIZED)
    machine.apply(Event.CONFIRMATION_OBSERVED)
    assert machine.state is State.CONFIRMED


def test_unknown_question_routes_to_review_not_submit():
    machine = StateMachine()
    for event in (
        Event.PROFILE_APPROVED,
        Event.SESSION_ACQUIRED,
        Event.JOB_OPENED,
        Event.FORM_DISCOVERED,
    ):
        machine.apply(event)
    machine.apply(Event.UNKNOWN_QUESTION)
    assert machine.state is State.REVIEW_REQUIRED
    with pytest.raises(InvalidTransition):
        machine.apply(Event.SUBMIT_AUTHORIZED)


def test_submission_verifier_requires_deterministic_signal():
    verifier = SubmissionVerifier()
    no_signal = Observation(
        url="https://ats.test/jobs/1/apply",
        success_text=None,
        application_id=None,
        submitted_control_clicked=True,
    )
    assert verifier.verify(no_signal) is None

    verified = verifier.verify(
        Observation(
            url="https://ats.test/applications/abc/confirmation",
            success_text="Application submitted",
            application_id="abc",
            submitted_control_clicked=True,
        )
    )
    assert verified is not None
    assert verified.application_id == "abc"


def test_recipe_compiler_promotes_only_verified_sanitized_trace(tmp_path):
    path = tmp_path / "queue.db"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE jobs (
          id TEXT PRIMARY KEY, portal TEXT, title TEXT, company TEXT,
          location TEXT, url TEXT NOT NULL, prio INTEGER DEFAULT 0,
          status TEXT DEFAULT 'pending', result TEXT, added_at TEXT,
          updated_at TEXT, claimed_by TEXT
        );
        CREATE TABLE applications (
          id INTEGER PRIMARY KEY, portal TEXT, company TEXT, role TEXT,
          status TEXT, applied_at TEXT, url TEXT
        );
        """
    )
    db.close()
    migrate_queue(path)
    compiler = RecipeCompiler(path)
    actions = [
        RecoveryAction("fill", "email", "candidate.email", "field", True),
        RecoveryAction("click", "continue", None, "button", True),
    ]

    with pytest.raises(ValueError, match="verified submission evidence"):
        compiler.promote("greenhouse", 1, actions, evidence=None)

    from workflow.models import SubmissionEvidence

    recipe = compiler.promote(
        "greenhouse",
        1,
        actions,
        evidence=SubmissionEvidence(
            observed_at="2026-08-19T10:00:00+00:00",
            success_text="Application submitted",
        ),
    )
    assert recipe.status == "verified"
    assert "candidate@example" not in recipe.document_json
    assert "candidate.email" in recipe.document_json

    db = sqlite3.connect(path)
    assert db.execute("SELECT status FROM recipes").fetchone() == ("verified",)
    db.close()
