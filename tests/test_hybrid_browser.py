from workflow.adapters.base import DriverAction, RuntimeContext
from workflow.hybrid_browser import HybridBrowserRuntime
from workflow.recovery import ActionCandidate, TraceAction


class Driver:
    def __init__(self):
        self.items = (ActionCandidate("c1", "button", "Continue"),)
        self.replayed = []

    def inspect(self, url):
        return self.items

    def replay(self, actions):
        self.replayed.extend(actions)
        return True


class Provider:
    def recover(self, request):
        return (TraceAction("click", "recover_navigation", "c1", "button"),)


def context():
    return RuntimeContext("session-1", "https://fixture.test/apply")


def test_hybrid_prefers_deterministic_recipe():
    driver = Driver()
    runtime = HybridBrowserRuntime(
        driver,
        recovery_provider=Provider(),
        adapter_resolver=lambda portal, intent: type(
            "Adapter", (), {"plan": lambda self, candidates, ctx: (DriverAction("click", intent, "c1"),)}
        )(),
    )
    result = runtime.run(context(), "fixture", "continue")
    assert result == type(result)(True, "playwright")
    assert len(driver.replayed) == 1


def test_hybrid_uses_browser_use_for_low_risk_drift_then_replays_playwright():
    driver = Driver()
    runtime = HybridBrowserRuntime(driver, recovery_provider=Provider())
    result = runtime.run(context(), "fixture", "recover_navigation")
    assert result == type(result)(True, "browser_use->playwright")
    assert driver.replayed[0].target_id == "c1"


def test_hybrid_rejects_recovered_terminal_action():
    class UnsafeProvider:
        def recover(self, request):
            return (TraceAction("click", "submit_application", "c1", "button"),)

    result = HybridBrowserRuntime(Driver(), recovery_provider=UnsafeProvider()).run(
        context(), "fixture", "submit_application"
    )
    assert not result.ok
    assert "terminal" in result.reason
