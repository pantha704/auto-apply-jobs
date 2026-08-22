from workflow.page_runtime import PlaywrightPageDriver
from workflow.adapters.base import DriverAction
from workflow.leases import FileSessionLease
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
