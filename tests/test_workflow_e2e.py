from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from workflow.engine import Event, InvalidTransition, State, StateMachine
from workflow.models import Outcome, OutcomeCode
from workflow.recipe import Recipe, RecipeCompiler, RecoveryAction
from workflow.schema import migrate_queue
from workflow.store import WorkflowStore
from workflow.verifier import Observation, SubmissionVerifier


FIXTURES = Path(__file__).parent / "fixtures" / "ats"
WORKER_ID = "fixture-worker"


@dataclass(frozen=True)
class SyntheticRun:
    state: State
    application_count: int
    recipe: Recipe | None = None


def _load_fixture(name: str) -> dict[str, object]:
    fixture = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert fixture["fixture_only"] is True
    assert str(fixture["url"]).startswith("https://synthetic-ats.invalid/")
    return fixture


def _queue(path: Path, fixture: dict[str, object]) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE jobs (
          id TEXT PRIMARY KEY, portal TEXT, url TEXT, title TEXT, source TEXT,
          status TEXT DEFAULT 'pending', claimed_by TEXT, result TEXT,
          prio INTEGER DEFAULT 0, posted_at TEXT, fetched_at TEXT
        );
        CREATE TABLE applications (
          id INTEGER PRIMARY KEY AUTOINCREMENT, portal TEXT NOT NULL,
          company TEXT, role TEXT, url TEXT NOT NULL, applied_at TEXT NOT NULL,
          answers TEXT, resume_used TEXT, status TEXT, note TEXT,
          snap_before TEXT, snap_after TEXT, url_hash TEXT,
          UNIQUE(portal, url_hash)
        );
        """
    )
    db.execute(
        "INSERT INTO jobs(id,portal,url,title,status,prio) VALUES(?,?,?,?,?,?)",
        ("fixture-job", fixture["adapter"], fixture["url"], fixture["role"], "pending", 10),
    )
    db.commit()
    db.close()
    migrate_queue(path)


def _application_count(path: Path) -> int:
    with sqlite3.connect(path) as db:
        return int(db.execute("SELECT COUNT(*) FROM applications").fetchone()[0])


def _drive_fixture(path: Path, fixture: dict[str, object]) -> SyntheticRun:
    """Test-only ATS driver exercising the real state, verifier, recipe, and store."""
    _queue(path, fixture)
    store = WorkflowStore(path)
    claim = store.claim_next(WORKER_ID, portal=str(fixture["adapter"]))
    assert claim is not None

    machine = StateMachine()
    for event in (
        Event.PROFILE_APPROVED,
        Event.SESSION_ACQUIRED,
        Event.JOB_OPENED,
        Event.FORM_DISCOVERED,
    ):
        machine.apply(event)

    questions = set(fixture["questions"])
    known_answers = set(fixture["known_answers"])
    if questions - known_answers:
        machine.apply(Event.UNKNOWN_QUESTION)
        store.finish(
            claim.run_id,
            claim.worker_id,
            claim.lease_token,
            Outcome(OutcomeCode.UNKNOWN_ANSWER, confirmed=False, retryable=False, safe_detail="fixture unknown question"),
        )
        return SyntheticRun(machine.state, _application_count(path))

    recipe = None
    recovery = fixture.get("recovery")
    if fixture["selector_status"] == "drifted" and recovery:
        actions = [RecoveryAction(**action) for action in recovery["actions"]]
        if not recovery["succeeds"]:
            with pytest.raises(ValueError, match="verified submission evidence"):
                RecipeCompiler(path).promote(
                    str(fixture["adapter"]), int(fixture["manifest_version"]), actions, evidence=None
                )
            machine.apply(Event.FAILURE)
            store.finish(
                claim.run_id,
                claim.worker_id,
                claim.lease_token,
                Outcome(OutcomeCode.UI_DRIFT, confirmed=False, retryable=True, safe_detail="fixture recovery failed"),
            )
            return SyntheticRun(machine.state, _application_count(path))

    machine.apply(Event.FORM_COMPLETED)
    machine.apply(Event.SUBMIT_AUTHORIZED)
    confirmation = fixture.get("confirmation") or {}
    evidence = SubmissionVerifier().verify(
        Observation(
            url=str(confirmation.get("url", fixture["url"])),
            success_text=confirmation.get("success_text"),
            application_id=confirmation.get("application_id"),
            submitted_control_clicked=bool(fixture["submit_clicked"]),
        )
    )
    if evidence is None:
        store.finish(
            claim.run_id,
            claim.worker_id,
            claim.lease_token,
            Outcome(
                OutcomeCode.CONFIRMATION_AMBIGUOUS,
                confirmed=False,
                retryable=True,
                safe_detail="fixture click without deterministic confirmation",
            ),
        )
        return SyntheticRun(machine.state, _application_count(path))

    machine.apply(Event.CONFIRMATION_OBSERVED)
    store.confirm_submission(
        claim.run_id,
        claim.worker_id,
        claim.lease_token,
        portal=str(fixture["adapter"]),
        company=str(fixture["company"]),
        role=str(fixture["role"]),
        url=str(fixture["url"]),
        evidence=evidence,
    )
    if recovery:
        actions = [RecoveryAction(**action) for action in recovery["actions"]]
        recipe = RecipeCompiler(path).promote(
            str(fixture["adapter"]), int(fixture["manifest_version"]), actions, evidence=evidence
        )
    return SyntheticRun(machine.state, _application_count(path), recipe)


def test_known_happy_path_reaches_confirmed_and_inserts_application(tmp_path: Path) -> None:
    result = _drive_fixture(tmp_path / "queue.db", _load_fixture("known_happy_path.json"))
    assert result.state is State.CONFIRMED
    assert result.application_count == 1


def test_unknown_question_routes_to_review_and_cannot_submit(tmp_path: Path) -> None:
    fixture = _load_fixture("unknown_question.json")
    result = _drive_fixture(tmp_path / "queue.db", fixture)
    assert result.state is State.REVIEW_REQUIRED
    assert result.application_count == 0
    machine = StateMachine(state=result.state)
    with pytest.raises(InvalidTransition):
        machine.apply(Event.SUBMIT_AUTHORIZED)


def test_clicked_submit_without_confirmation_never_inserts_application(tmp_path: Path) -> None:
    result = _drive_fixture(
        tmp_path / "queue.db", _load_fixture("clicked_submit_no_confirmation.json")
    )
    assert result.state is State.SUBMITTING
    assert result.application_count == 0


def test_verified_confirmation_inserts_application(tmp_path: Path) -> None:
    path = tmp_path / "queue.db"
    result = _drive_fixture(path, _load_fixture("verified_confirmation.json"))
    assert result.state is State.CONFIRMED
    assert result.application_count == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT status FROM applications").fetchone() == ("submitted",)
        assert db.execute("SELECT confirmed FROM application_runs").fetchone() == (1,)


def test_successful_recovery_promotes_only_sanitized_recipe(tmp_path: Path) -> None:
    fixture = _load_fixture("selector_drift_recovery.json")
    result = _drive_fixture(tmp_path / "queue.db", fixture)
    assert result.state is State.CONFIRMED
    assert result.application_count == 1
    assert result.recipe is not None
    document = json.loads(result.recipe.document_json)
    assert result.recipe.status == "verified"
    assert [action["input_ref"] for action in document["actions"]] == ["candidate.email", None]
    assert str(fixture["company"]) not in result.recipe.document_json
    assert str(fixture["url"]) not in result.recipe.document_json


def test_failed_recovery_cannot_promote_recipe(tmp_path: Path) -> None:
    path = tmp_path / "queue.db"
    result = _drive_fixture(path, _load_fixture("selector_drift_failed_recovery.json"))
    assert result.state is State.FAILED
    assert result.application_count == 0
    assert result.recipe is None
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM recipes").fetchone() == (0,)
