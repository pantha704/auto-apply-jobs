from __future__ import annotations

from collections.abc import Sequence

from .adapters.base import DriverAction
from .recovery import ActionCandidate


class PlaywrightPageDriver:
    """BrowserDriver facade over the already-owned Playwright page."""

    def __init__(self, page):
        self.page = page
        self.targets = {}

    def inspect(self, url: str) -> Sequence[ActionCandidate]:
        self.targets.clear()
        out = []
        locator = self.page.locator("button,a[href],[role=button],[role=link]")
        for i in range(locator.count()):
            item = locator.nth(i)
            try:
                if not item.is_visible() or not item.is_enabled():
                    continue
                role = (item.get_attribute("role") or ("link" if item.get_attribute("href") else "button")).lower()
                label = (item.get_attribute("aria-label") or item.inner_text() or "").strip()[:160]
                if not label:
                    continue
                candidate = ActionCandidate(f"c{i}", role if role in {"button", "link"} else "button", label)
                if candidate.actionable:
                    self.targets[candidate.candidate_id] = item
                    out.append(candidate)
            except Exception:
                continue
        return tuple(out)

    def replay(self, actions: Sequence[DriverAction]) -> bool:
        for action in actions:
            target = self.targets.get(action.target_id)
            if target is None or action.action_type != "click":
                return False
            try:
                target.click()
            except Exception:
                return False
        return True
