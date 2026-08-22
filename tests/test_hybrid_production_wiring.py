from workflow.page_runtime import PlaywrightPageDriver
from workflow.adapters.base import DriverAction
from workflow.leases import FileSessionLease
import dynamic_ui
from pathlib import Path
import pytest


class Element:
    def __init__(self, label):
        self.label = label
        self.clicked = False
    def is_visible(self): return True
    def is_enabled(self): return True
    def get_attribute(self, name): return self.label if name == "aria-label" else None
    def inner_text(self): return self.label
    def click(self): self.clicked = True


class Locator:
    def __init__(self, items): self.items = items
    def count(self): return len(self.items)
    def nth(self, i): return self.items[i]


class Page:
    url = "https://fixture.test/job"
    def __init__(self): self.items = [Element("Apply Now")]
    def locator(self, _): return Locator(self.items)


def test_page_driver_replays_only_inventory_targets():
    page = Page()
    driver = PlaywrightPageDriver(page)
    candidates = driver.inspect(page.url)
    assert candidates[0].label == "Apply Now"
    assert driver.replay((DriverAction("click", "apply", candidates[0].candidate_id),))
    assert page.items[0].clicked


def test_page_driver_fails_closed_for_unknown_or_non_click_actions():
    page = Page()
    driver = PlaywrightPageDriver(page)
    driver.inspect(page.url)
    assert not driver.replay((DriverAction("fill", "apply", "c0"),))
    assert not driver.replay((DriverAction("click", "apply", "missing"),))


def test_file_session_lease_creates_private_reusable_lock(tmp_path):
    lease = FileSessionLease(tmp_path / "leases")
    with lease.acquire("wellfound:wf-w1"):
        lock = tmp_path / "leases" / "wellfound_wf-w1.lock"
        assert lock.is_file()
        assert lock.stat().st_mode & 0o777 == 0o600
    with lease.acquire("wellfound:wf-w1"):
        assert lock.is_file()


@pytest.mark.parametrize(
    ("worker", "intent"),
    [
        ("worker_wellfound.py", '"apply"'),
        ("worker_internshala.py", '"apply"'),
        ("worker_internshala.py", '"proceed"'),
        ("worker_yc.py", '"apply"'),
        ("worker_linkedin.py", '"easy_apply"'),
        ("worker_external.py", '"apply"'),
    ],
)
def test_browser_workers_wire_low_risk_navigation_through_hybrid(worker, intent):
    source = (Path(__file__).parents[1] / worker).read_text(encoding="utf-8")
    assert "dynamic_ui.hybrid_click(" in source
    assert intent in source
    assert "--remote-debugging-address=127.0.0.1" in source


def test_wwr_apply_transition_uses_hybrid_replay_and_cdp():
    source = (Path(__file__).parents[1] / "worker_external.py").read_text(encoding="utf-8")
    start = source.index("def wwr_apply")
    end = source.index("def main", start)
    function = source[start:end]
    assert 'dynamic_ui.hybrid_click(' in function
    assert '"weworkremotely", "apply"' in function
    assert 'postcondition=' in function
    assert 'f"--remote-debugging-port={CDP_PORT}"' in function
    assert "return (True" not in function
    assert "external-ats-route-required" in function


def test_auxiliary_workers_delegate_to_hybrid_enabled_apply_functions():
    root = Path(__file__).parents[1]
    recheck = (root / "worker_wf_recheck.py").read_text(encoding="utf-8")
    review = (root / "worker_review.py").read_text(encoding="utf-8")
    assert "ww.apply_one(" in recheck
    assert "ww.apply_one(" in review
    assert "wy.apply_url(" in review
    assert "wex.route(" in review


@pytest.mark.parametrize("intent", ["submit", "send", "finalize"])
def test_hybrid_click_rejects_terminal_actions(intent):
    with pytest.raises(ValueError, match="deterministic-only"):
        dynamic_ui.hybrid_click(None, "fixture", intent, "http://127.0.0.1:1")


def test_linkedin_nonterminal_modal_steps_are_hybrid_but_submit_is_not():
    source = (Path(__file__).parents[1] / "worker_linkedin.py").read_text(encoding="utf-8")
    assert "def hybrid_modal_click" in source
    for intent in ("next", "review", "save", "add_work_experience"):
        assert f'"{intent}"' in source
    terminal = source[source.index("def click_terminal_submit"):]
    assert "dynamic_ui.hybrid_click" not in terminal
    assert "Submit application" in terminal


def test_wellfound_sso_reentry_and_himalayas_upsell_use_hybrid():
    root = Path(__file__).parents[1]
    wellfound = (root / "worker_wellfound.py").read_text(encoding="utf-8")
    external = (root / "worker_external.py").read_text(encoding="utf-8")
    assert '"wellfound", "login"' in wellfound
    assert '"wellfound", "apply"' in wellfound
    assert '"himalayas", "dismiss_upsell"' in external
    assert '"himalayas", "ready"' in external
