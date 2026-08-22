from __future__ import annotations

from contextlib import contextmanager

from workflow.adapters.base import RuntimeContext
from workflow.adapters.generic import GenericDeterministicAdapter
from workflow.recovery import ActionCandidate, ActionRisk, RecoveryRequest, TraceAction
from workflow.runner import Route, RuntimeRouter


class Lease:
    @contextmanager
    def acquire(self, session_id):
        yield


class Provider:
    def __init__(self, action):
        self.action = action
        self.requests = []

    def recover(self, request):
        self.requests.append(request)
        return (self.action,)


class Driver:
    def __init__(self, observations):
        self.observations = list(observations)
        self.inspect_calls = 0
        self.executed = []

    def inspect(self, url):
        index = min(self.inspect_calls, len(self.observations) - 1)
        self.inspect_calls += 1
        return self.observations[index]

    def replay(self, actions):
        self.executed.extend(actions)
        return True


def context():
    return RuntimeContext("wellfound:w1", "https://wellfound.com/jobs/123")


def test_recovery_request_has_bounded_context_and_stable_fingerprint():
    request = RecoveryRequest(
        (ActionCandidate("c1", "button", "Continue"),),
        site_id="wellfound",
        intent="continue_application",
        page_fingerprint="sha256:abc",
    )
    assert request.site_id == "wellfound"
    assert request.intent == "continue_application"
    assert request.page_fingerprint == "sha256:abc"


def test_terminal_labels_are_structurally_high_risk_even_with_safe_sounding_intent():
    candidate = ActionCandidate("c1", "button", "Confirm and submit application")
    assert candidate.risk is ActionRisk.TERMINAL
    action = TraceAction("click", "continue", "c1", "button")
    assert action.risk_for(candidate) is ActionRisk.TERMINAL


def test_shadow_mode_uses_browser_use_as_eyes_without_executing_proposal():
    controls = (ActionCandidate("c1", "button", "Complex next step"),)
    driver = Driver([controls])
    provider = Provider(TraceAction("click", "continue", "c1", "button"))

    result = RuntimeRouter(
        driver,
        Lease(),
        GenericDeterministicAdapter(),
        provider,
        recovery_mode="shadow",
    ).run(context())

    assert result.route is Route.RECOVERY_SHADOW
    assert result.trace == (TraceAction("click", "continue", "c1", "button"),)
    assert driver.executed == []
    assert provider.requests[0].page_fingerprint.startswith("sha256:")


def test_recovery_rechecks_page_fingerprint_before_playwright_executes():
    before = (ActionCandidate("c1", "button", "Complex next step"),)
    after = (ActionCandidate("c9", "button", "Different page"),)
    driver = Driver([before, after])
    provider = Provider(TraceAction("click", "continue", "c1", "button"))

    result = RuntimeRouter(
        driver,
        Lease(),
        GenericDeterministicAdapter(),
        provider,
        recovery_mode="execute",
    ).run(context())

    assert result.route is Route.OPERATOR
    assert result.review_reason == "stale recovery observation"
    assert driver.executed == []


def test_execute_mode_requires_postcondition_verifier():
    controls = (ActionCandidate("c1", "button", "Complex next step"),)
    driver = Driver([controls])
    provider = Provider(TraceAction("click", "continue", "c1", "button"))

    result = RuntimeRouter(
        driver, Lease(), GenericDeterministicAdapter(), provider, recovery_mode="execute"
    ).run(context())

    assert result.route is Route.OPERATOR
    assert result.review_reason == "recovery postcondition unavailable"
    assert driver.executed == []


def test_verified_postcondition_is_required_before_learning():
    controls = (ActionCandidate("c1", "button", "Complex next step"),)
    learned = []
    failed_driver = Driver([controls])
    failed = RuntimeRouter(
        failed_driver,
        Lease(),
        GenericDeterministicAdapter(),
        Provider(TraceAction("click", "continue", "c1", "button")),
        recovery_mode="execute",
        recovery_verifier=lambda context, trace: False,
        verified_learner=lambda context, trace: learned.append(trace),
    ).run(context())
    assert failed.route is Route.OPERATOR
    assert failed.review_reason == "recovery postcondition failed"
    assert learned == []

    passed_driver = Driver([controls])
    passed = RuntimeRouter(
        passed_driver,
        Lease(),
        GenericDeterministicAdapter(),
        Provider(TraceAction("click", "continue", "c1", "button")),
        recovery_mode="execute",
        recovery_verifier=lambda context, trace: True,
        verified_learner=lambda context, trace: learned.append(trace),
    ).run(context())
    assert passed.route is Route.RECOVERY
    assert learned == [passed.trace]


def test_sensitive_label_corpus_is_rejected():
    import pytest

    labels = (
        "Call +91 98765 43210",
        "Continue https://example.test/app?token=secret",
        "Authorization Bearer abcdefghijklmnop",
        "OTP 482193",
    )
    for label in labels:
        with pytest.raises(ValueError, match="sensitive"):
            ActionCandidate("c1", "button", label)


def test_action_role_incompatibility_is_rejected_before_replay():
    controls = (ActionCandidate("c1", "button", "Continue"),)
    driver = Driver([controls])
    provider = Provider(TraceAction("fill", "continue", "c1", "button"))
    result = RuntimeRouter(driver, Lease(), GenericDeterministicAdapter(), provider).run(context())
    assert result.route is Route.OPERATOR
    assert driver.executed == []


def test_recovery_never_executes_terminal_candidate():
    controls = (ActionCandidate("c1", "button", "Send application"),)
    driver = Driver([controls])
    provider = Provider(TraceAction("click", "continue", "c1", "button"))

    result = RuntimeRouter(
        driver,
        Lease(),
        GenericDeterministicAdapter(),
        provider,
        recovery_mode="execute",
    ).run(context())

    assert result.route is Route.OPERATOR
    assert result.review_reason == "unsafe recovery plan"
    assert driver.executed == []
