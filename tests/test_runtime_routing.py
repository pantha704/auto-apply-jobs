from __future__ import annotations

from contextlib import contextmanager

import pytest

from workflow.adapters.base import DriverAction, RuntimeContext
from workflow.adapters.generic import GenericDeterministicAdapter
from workflow.providers import RecoveryProvider
from workflow.recovery import ActionCandidate, RecoveryRequest, TraceAction
from workflow.runner import Route, RuntimeRouter


class FakeDriver:
    def __init__(self, candidates=()):
        self.candidates = tuple(candidates)
        self.executed: list[DriverAction] = []

    def inspect(self, url: str):
        return self.candidates

    def replay(self, actions):
        self.executed.extend(actions)
        return True


class FakeLease:
    def __init__(self):
        self.active = False
        self.entries = 0

    @contextmanager
    def acquire(self, session_id: str):
        self.entries += 1
        self.active = True
        try:
            yield
        finally:
            self.active = False


class StubRecipe:
    verified = True

    def __init__(self, actions=()):
        self.actions = tuple(actions)
        self.calls = 0

    def replay(self, driver, context):
        self.calls += 1
        driver.replay(self.actions)
        return True


class StubRecovery(RecoveryProvider):
    def __init__(self, actions=()):
        self.actions = tuple(actions)
        self.requests: list[RecoveryRequest] = []

    def recover(self, request: RecoveryRequest):
        self.requests.append(request)
        return self.actions


def context():
    return RuntimeContext(session_id="session-1", url="https://jobs.test/apply")


def test_verified_recipe_has_first_priority_and_runs_inside_lease():
    driver = FakeDriver()
    lease = FakeLease()
    recipe = StubRecipe([DriverAction("click", "continue", "button-1")])
    adapter = GenericDeterministicAdapter()
    provider = StubRecovery()

    result = RuntimeRouter(driver, lease, adapter, provider).run(context(), recipe=recipe)

    assert result.route is Route.RECIPE
    assert recipe.calls == 1
    assert lease.entries == 1 and not lease.active
    assert provider.requests == []


def test_deterministic_adapter_precedes_recovery():
    candidate = ActionCandidate("field-1", "field", "Email address")
    driver = FakeDriver([candidate])
    provider = StubRecovery()

    result = RuntimeRouter(driver, FakeLease(), GenericDeterministicAdapter(), provider).run(context())

    assert result.route is Route.DETERMINISTIC
    assert driver.executed == [DriverAction("fill", "email", "field-1")]
    assert provider.requests == []


def test_deterministic_replay_must_satisfy_available_postcondition():
    candidate = ActionCandidate("field-1", "field", "Email address")
    driver = FakeDriver([candidate])
    result = RuntimeRouter(
        driver, FakeLease(), GenericDeterministicAdapter(), StubRecovery(),
        recovery_verifier=lambda context, trace: False,
    ).run(context())
    assert result.route is Route.OPERATOR
    assert result.review_reason == "deterministic postcondition failed"


def test_deterministic_plan_cannot_click_a_terminal_label_for_low_risk_intent():
    class UnsafeSubstringAdapter:
        name = "unsafe-substring"

        def plan(self, candidates, context):
            return (DriverAction("click", "save", "danger-1"),)

    candidate = ActionCandidate(
        "danger-1", "button", "Save and submit application"
    )
    driver = FakeDriver([candidate])
    result = RuntimeRouter(
        driver, FakeLease(), UnsafeSubstringAdapter(), StubRecovery()
    ).run(context())
    assert result.route is Route.OPERATOR
    assert result.review_reason == "unsafe deterministic plan"
    assert driver.executed == []


def test_recovery_receives_only_sanitized_actionable_candidates_and_returns_trace():
    candidates = (
        ActionCandidate("mystery-1", "button", "Continue"),
        ActionCandidate("text-1", "text", "Privacy policy"),
    )
    driver = FakeDriver(candidates)
    provider = StubRecovery([TraceAction("click", "continue", "mystery-1", "button")])

    result = RuntimeRouter(
        driver,
        FakeLease(),
        GenericDeterministicAdapter(),
        provider,
        recovery_verifier=lambda context, trace: True,
    ).run(context())

    assert result.route is Route.RECOVERY
    assert result.trace == provider.actions
    assert provider.requests[0].candidates == (candidates[0],)
    payload = repr(provider.requests[0])
    assert "https://" not in payload
    assert "cookie" not in payload.lower()


def test_recovery_preserves_the_callers_exact_low_risk_intent():
    candidate = ActionCandidate("apply-1", "button", "Apply now")
    driver = FakeDriver([candidate])
    provider = StubRecovery([TraceAction("click", "apply", "apply-1", "button")])
    result = RuntimeRouter(
        driver, FakeLease(), GenericDeterministicAdapter(), provider,
        recovery_verifier=lambda context, trace: True,
    ).run(context(), recovery_intent="apply")
    assert result.route is Route.RECOVERY
    assert provider.requests[0].intent == "apply"


def test_recovery_rejects_a_different_intent_than_requested():
    candidate = ActionCandidate("other-1", "button", "Continue")
    driver = FakeDriver([candidate])
    provider = StubRecovery([TraceAction("click", "continue", "other-1", "button")])
    result = RuntimeRouter(
        driver, FakeLease(), GenericDeterministicAdapter(), provider,
        recovery_verifier=lambda context, trace: True,
    ).run(context(), recovery_intent="apply")
    assert result.route is Route.OPERATOR
    assert driver.executed == []


def test_recovery_cannot_choose_or_execute_final_submit():
    provider = StubRecovery([TraceAction("click", "submit", "submit-1", "button")])
    driver = FakeDriver([ActionCandidate("submit-1", "button", "Submit application")])

    result = RuntimeRouter(driver, FakeLease(), GenericDeterministicAdapter(), provider).run(context())

    assert result.route is Route.OPERATOR
    assert result.review_required
    assert driver.executed == []


def test_no_provider_is_valid_and_routes_to_review_without_fabrication():
    driver = FakeDriver([ActionCandidate("unknown-1", "button", "Next")])

    result = RuntimeRouter(driver, FakeLease(), GenericDeterministicAdapter(), None).run(context())

    assert result.route is Route.OPERATOR
    assert result.review_required
    assert result.trace == ()
    assert driver.executed == []


def test_candidate_schema_rejects_values_html_and_cookie_material():
    for label in ("<input value='secret'>", "session_cookie=abc", "a@private.test"):
        with pytest.raises(ValueError):
            ActionCandidate("field-1", "field", label)
