from __future__ import annotations

import pytest

from workflow.adapters.base import DriverAction
from workflow.browser_runtime import (
    BrowserUseCDPRecoveryProvider,
    PlaywrightCDPDriver,
    require_loopback_cdp,
)
from workflow.recovery import ActionCandidate, RecoveryRequest, TraceAction


class FakeElement:
    def __init__(self, tag, attrs=None, text=""):
        self.tag, self.attrs, self.text = tag, attrs or {}, text
        self.filled = None
        self.clicked = False

    def is_visible(self): return True
    def is_enabled(self): return True
    def evaluate(self, _): return self.tag
    def get_attribute(self, name): return self.attrs.get(name)
    def inner_text(self): return self.text
    def fill(self, value): self.filled = value
    def click(self): self.clicked = True
    def check(self): self.clicked = True
    def select_option(self, value): self.filled = value


class FakeLocator:
    def __init__(self, items): self.items = items
    def count(self): return len(self.items)
    def nth(self, index): return self.items[index]


class FakePage:
    def __init__(self, items): self.url = "about:blank"; self.items = items
    def goto(self, url, wait_until): self.url = url
    def locator(self, _): return FakeLocator(self.items)


class FakeContext:
    def __init__(self, page): self.pages = [page]


class FakeBrowser:
    def __init__(self, page): self.contexts = [FakeContext(page)]


class FakeSession:
    instance = None

    def __init__(self, **kwargs):
        self.kwargs, self.started, self.stopped = kwargs, False, False
        FakeSession.instance = self

    async def start(self): self.started = True
    async def stop(self): self.stopped = True


def test_cdp_endpoint_is_loopback_only():
    assert require_loopback_cdp("http://127.0.0.1:9333/") == "http://127.0.0.1:9333"
    for value in ("http://10.0.0.2:9333", "https://example.test:9333", "file:///tmp/socket", "http://localhost"):
        with pytest.raises(ValueError):
            require_loopback_cdp(value)


def test_playwright_driver_resolves_values_locally_and_blocks_submit():
    email = FakeElement("input", {"name": "email", "value": "must-not-be-read"})
    button = FakeElement("button", text="Continue")
    page = FakePage([email, button])
    driver = PlaywrightCDPDriver(
        "http://127.0.0.1:9333",
        lambda intent: "private@example.test" if intent == "email" else None,
        connector=lambda _: FakeBrowser(page),
    )
    candidates = driver.inspect("https://synthetic-ats.invalid/apply")
    assert [(item.role, item.label) for item in candidates] == [("field", "email"), ("button", "Continue")]
    assert "must-not-be-read" not in repr(candidates)
    assert driver.replay([DriverAction("fill", "email", candidates[0].candidate_id)])
    assert email.filled == "private@example.test"
    assert not driver.replay([DriverAction("click", "submit_application", candidates[1].candidate_id)])
    assert button.clicked is False


def test_browser_use_attaches_locally_and_planner_receives_only_sanitized_request():
    seen = []
    provider = BrowserUseCDPRecoveryProvider(
        "http://127.0.0.1:9333",
        lambda request: seen.append(request) or [TraceAction("click", "continue", "c1", "button")],
        session_factory=FakeSession,
    )
    request = RecoveryRequest((ActionCandidate("c1", "button", "Continue"),))
    actions = provider.recover(request)
    assert tuple(actions) == (TraceAction("click", "continue", "c1", "button"),)
    assert seen == [request]
    assert FakeSession.instance.kwargs == {"cdp_url": "http://127.0.0.1:9333", "keep_alive": True}
    assert FakeSession.instance.started and FakeSession.instance.stopped
