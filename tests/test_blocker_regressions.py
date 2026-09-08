"""Worker-level regression tests for the five merge blockers (B1–B5) + B6.

These exercise the critical paths the green suite missed: pause gates on
terminal functions, fence ownership on mark/finalize, post-submit crash
handling, the Next->Submit bypass, service caller args, and bundle wiring.
"""
from __future__ import annotations

import pathlib
import sqlite3
from unittest.mock import patch

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def _jobs_db(tmp_path):
    path = tmp_path / "queue.db"
    c = sqlite3.connect(path)
    c.execute(
        """CREATE TABLE jobs (
          id TEXT PRIMARY KEY, portal TEXT, url TEXT, title TEXT,
          status TEXT DEFAULT 'pending', claimed_by TEXT, result TEXT,
          claimed_at TEXT, lease_expires_at TEXT,
          attempt_count INTEGER NOT NULL DEFAULT 0,
          last_outcome_code TEXT, last_fence_token TEXT)"""
    )
    c.commit()
    return path, c


# --- B2: pause gate on terminal functions ---

def test_linkedin_terminal_submit_refuses_when_paused():
    import worker_linkedin
    with patch("workflow.farm_control.can_send", return_value=False):
        assert worker_linkedin.click_terminal_submit(object()) is False


# --- B3: fence ownership ---

def test_guarded_mark_refuses_non_owner(tmp_path):
    from workflow.application_gate import guarded_mark
    _, c = _jobs_db(tmp_path)
    c.execute(
        "INSERT INTO jobs (id, status, claimed_by, last_fence_token) VALUES (?,?,?,?)",
        ("j1", "claimed", "wf-w1", "tok-abc"),
    )
    c.commit()
    rows = guarded_mark(c, "j1", "li-w9", "skip", "other-worker-write", fence_token="tok-abc")
    assert rows == 0
    assert c.execute("SELECT status FROM jobs WHERE id='j1'").fetchone()[0] == "claimed"
    c.close()


def test_guarded_mark_refuses_stale_fence(tmp_path):
    from workflow.application_gate import guarded_mark
    _, c = _jobs_db(tmp_path)
    c.execute(
        "INSERT INTO jobs (id, status, claimed_by, last_fence_token) VALUES (?,?,?,?)",
        ("j1", "claimed", "wf-w1", "tok-abc"),
    )
    c.commit()
    rows = guarded_mark(c, "j1", "wf-w1", "skip", "stale", fence_token="tok-old")
    assert rows == 0
    c.close()


def test_guarded_mark_accepts_owner_with_fence(tmp_path):
    from workflow.application_gate import guarded_mark
    _, c = _jobs_db(tmp_path)
    c.execute(
        "INSERT INTO jobs (id, status, claimed_by, last_fence_token) VALUES (?,?,?,?)",
        ("j1", "claimed", "wf-w1", "tok-abc"),
    )
    c.commit()
    rows = guarded_mark(c, "j1", "wf-w1", "skip", "done-fine", fence_token="tok-abc")
    assert rows == 1
    c.close()


def test_finalize_requires_worker_id(tmp_path):
    from audit import SubmissionFinalizer
    path, c = _jobs_db(tmp_path)
    c.execute(
        "INSERT INTO jobs (id, portal, status, claimed_by) VALUES (?,?,?,?)",
        ("j1", "linkedin", "claimed", "li-w1"),
    )
    c.commit()
    c.close()
    with pytest.raises(ValueError, match="worker_id required"):
        SubmissionFinalizer(str(path)).finalize(
            job_id="j1", status="done", result="x", portal="linkedin",
            url="https://example.com/j/1", audit_status="submitted",
        )


def test_finalize_refuses_other_owner(tmp_path):
    from audit import SubmissionFinalizer
    path, c = _jobs_db(tmp_path)
    c.execute(
        "INSERT INTO jobs (id, portal, status, claimed_by, last_fence_token)"
        " VALUES (?,?,?,?,?)",
        ("j1", "linkedin", "claimed", "li-w1", "tok-abc"),
    )
    c.commit()
    c.close()
    with pytest.raises(ValueError, match="owned by li-w1 not li-w9"):
        SubmissionFinalizer(str(path)).finalize(
            job_id="j1", status="done", result="x", portal="linkedin",
            url="https://example.com/j/1", audit_status="submitted",
            worker_id="li-w9", fence_token="tok-abc",
        )


def test_finalize_refuses_stale_fence(tmp_path):
    from audit import SubmissionFinalizer
    path, c = _jobs_db(tmp_path)
    c.execute(
        "INSERT INTO jobs (id, portal, status, claimed_by, last_fence_token)"
        " VALUES (?,?,?,?,?)",
        ("j1", "linkedin", "claimed", "li-w1", "tok-abc"),
    )
    c.commit()
    c.close()
    with pytest.raises(ValueError, match="stale fence"):
        SubmissionFinalizer(str(path)).finalize(
            job_id="j1", status="done", result="x", portal="linkedin",
            url="https://example.com/j/1", audit_status="submitted",
            worker_id="li-w1", fence_token="tok-old",
        )


# --- B4: uncertain submissions can never return to pending ---

def test_wedge_timeout_cannot_requeue_uncertain_job(tmp_path):
    from workflow.application_gate import guarded_mark
    _, c = _jobs_db(tmp_path)
    c.execute(
        "INSERT INTO jobs (id, status, claimed_by, last_fence_token, result)"
        " VALUES (?,?,?,?,?)",
        ("j1", "skip", "li-w1", "tok-abc", "submit-unconfirmed"),
    )
    c.commit()
    with pytest.raises(ValueError, match="cannot requeue"):
        guarded_mark(c, "j1", "li-w1", "pending", "browser-wedge-timeout",
                     fence_token="tok-abc")
    c.close()


def test_hard_timeout_cannot_requeue_submitted_job(tmp_path):
    from workflow.application_gate import guarded_mark
    _, c = _jobs_db(tmp_path)
    c.execute(
        "INSERT INTO jobs (id, status, claimed_by, last_fence_token, last_outcome_code)"
        " VALUES (?,?,?,?,?)",
        ("j1", "done", "li-w1", "tok-abc", "submitted"),
    )
    c.commit()
    with pytest.raises(ValueError, match="cannot requeue"):
        guarded_mark(c, "j1", "li-w1", "pending", "reviewed|hard-timeout",
                     fence_token="tok-abc")
    c.close()


def test_finalize_refuses_uncertain_becoming_done(tmp_path):
    from audit import SubmissionFinalizer
    path, c = _jobs_db(tmp_path)
    c.execute(
        "INSERT INTO jobs (id, portal, status, claimed_by) VALUES (?,?,?,?)",
        ("j1", "linkedin", "claimed", "li-w1"),
    )
    c.commit()
    c.close()
    with pytest.raises(ValueError, match="uncertain outcome cannot become done"):
        SubmissionFinalizer(str(path)).finalize(
            job_id="j1", status="done", result="submit-unconfirmed",
            portal="linkedin", url="https://example.com/j/1",
            audit_status="submitted", worker_id="li-w1",
        )


# --- B5: Next -> Submit bypass ---

def test_next_intent_to_submit_control_is_blocked():
    from dynamic_ui import validate_action_candidate
    controls = [
        {"id": "c0", "role": "button", "name": "Next"},
        {"id": "c1", "role": "button", "name": "Submit application"},
    ]
    # candidate carries ONLY candidate_id — the bypass check must resolve it.
    with pytest.raises(ValueError, match="does not match terminal label"):
        validate_action_candidate({"candidate_id": "c1"}, controls,
                                  intent="next", source="recovery")


def test_submit_intent_to_submit_control_passes():
    from dynamic_ui import validate_action_candidate
    controls = [{"id": "c1", "role": "button", "name": "Submit application"}]
    resolved = validate_action_candidate({"candidate_id": "c1"}, controls,
                                         intent="submit", source="recovery")
    assert resolved["id"] == "c1"


# --- B1: service callers pass an explicit reason ---

def test_all_reset_claims_service_callers_pass_reason():
    import re
    callers = list(REPO.glob("*.service")) + list((REPO / "deploy").glob("*.service"))
    checked = 0
    for svc in callers:
        text = svc.read_text()
        if "reset_claims.sh" not in text:
            continue
        checked += 1
        for line in text.splitlines():
            if "reset_claims.sh" in line:
                assert len(line.split()) >= 4, f"{svc.name}: missing reason arg: {line}"
    assert checked == 5, f"expected 5 reset_claims callers, found {checked}"


# --- B6: bundle registry + freshness ---

def test_claim_registry_round_trip():
    from workflow.application_gate import (
        remember_claim, claim_fence, claim_bundle, forget_claim,
    )
    remember_claim("jx", fence_token="tok-x", bundle="B")
    assert claim_fence("jx") == "tok-x"
    assert claim_bundle("jx") == "B"
    forget_claim("jx")
    assert claim_fence("jx") == ""
    assert claim_bundle("jx") is None


def test_bundle_matches_runtime():
    from workflow.release_bundle import ReleaseBundle
    from types import MappingProxyType

    class Policy:
        id = "pol-1"
        version = 3

    class Runtime:
        profile_id = "p-1"
        profile_revision = 2
        resume_id = "r-1"
        preference_set = Policy()

    bundle = ReleaseBundle(
        portal="linkedin", profile_id="p-1", profile_revision=2,
        resume_id="r-1", resume_path="/r.pdf", preference_set_id="pol-1",
        preference_set_version=3, session_revision=7,
        session_state=MappingProxyType({}), facts=MappingProxyType({}),
        claim_id="j1", claim_token="t",
    )
    assert bundle.matches_runtime(Runtime()) is True
    Runtime.profile_revision = 99
    assert bundle.matches_runtime(Runtime()) is False
    assert bundle.matches_runtime(object()) is False
