from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urlparse

from .adapters.base import DriverAction
from .providers import RecoveryProvider
from .recovery import ActionCandidate, RecoveryRequest, TraceAction


def require_loopback_cdp(cdp_url: str) -> str:
    parsed = urlparse(cdp_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise ValueError("CDP endpoint must be HTTP(S) on loopback")
    if parsed.port is None:
        raise ValueError("CDP endpoint requires an explicit port")
    return cdp_url.rstrip("/")


class PlaywrightCDPDriver:
    """Deterministic Playwright client attached to an owner-managed CloakBrowser."""

    def __init__(
        self,
        cdp_url: str,
        value_resolver: Callable[[str], Any | None],
        *,
        connector: Callable[[str], Any] | None = None,
    ) -> None:
        self.cdp_url = require_loopback_cdp(cdp_url)
        self.value_resolver = value_resolver
        self._playwright = None
        if connector is None:
            from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]

            self._playwright = sync_playwright().start()
            connector = self._playwright.chromium.connect_over_cdp
        if connector is None:
            raise RuntimeError("Playwright CDP connector is unavailable")
        self.browser = connector(self.cdp_url)
        if not self.browser.contexts:
            raise RuntimeError("CDP browser has no context")
        context = self.browser.contexts[0]
        self.page = context.pages[0] if context.pages else context.new_page()
        self._targets: dict[str, Any] = {}

    def inspect(self, url: str) -> tuple[ActionCandidate, ...]:
        if self.page.url != url:
            self.page.goto(url, wait_until="domcontentloaded")
        self._targets.clear()
        candidates: list[ActionCandidate] = []
        locator = self.page.locator("button,input,textarea,select,a[href]")
        for index in range(locator.count()):
            item = locator.nth(index)
            if not item.is_visible() or not item.is_enabled():
                continue
            tag = item.evaluate("el => el.tagName.toLowerCase()")
            input_type = (item.get_attribute("type") or "").casefold()
            role = self._role(tag, input_type)
            label = self._label(item, role)
            candidate_id = f"candidate-{len(candidates) + 1}"
            try:
                candidate = ActionCandidate(candidate_id, role, label)
            except ValueError:
                continue
            self._targets[candidate_id] = item
            candidates.append(candidate)
        return tuple(candidates)

    def replay(self, actions: Sequence[DriverAction]) -> bool:
        for action in actions:
            if action.intent.casefold() in {
                "submit",
                "final_submit",
                "submit_application",
            }:
                return False
            target = self._targets.get(action.target_id)
            if target is None:
                return False
            try:
                if action.action_type == "click":
                    target.click()
                elif action.action_type == "check":
                    target.check()
                elif action.action_type in {"fill", "select"}:
                    value = self.value_resolver(action.intent)
                    if value is None:
                        return False
                    if action.action_type == "fill":
                        target.fill(str(value))
                    else:
                        target.select_option(str(value))
                else:
                    return False
            except Exception:
                return False
        return True

    def close(self) -> None:
        # Stop the client transport only; never Browser.close the owner session.
        if self._playwright is not None:
            self._playwright.stop()

    @staticmethod
    def _role(tag: str, input_type: str) -> str:
        if tag == "select":
            return "select"
        if tag == "input" and input_type in {"checkbox", "radio"}:
            return "checkbox"
        if tag in {"input", "textarea"} and input_type not in {
            "button",
            "submit",
            "reset",
        }:
            return "field"
        if tag == "a":
            return "link"
        return "button"

    @staticmethod
    def _label(item: Any, role: str) -> str:
        for attribute in ("aria-label", "placeholder", "name", "title"):
            value = item.get_attribute(attribute)
            if value and value.strip():
                return value.strip()[:160]
        text = (item.inner_text() or "").strip()
        if text:
            return text[:160]
        return role


class BrowserUseCDPRecoveryProvider(RecoveryProvider):
    """Attach Browser Use to CloakBrowser, then plan from sanitized IDs only."""

    def __init__(
        self,
        cdp_url: str,
        planner: Callable[[RecoveryRequest], Sequence[TraceAction]],
        *,
        session_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.cdp_url = require_loopback_cdp(cdp_url)
        self.planner = planner
        self.session_factory = session_factory

    async def _verify_attachment(self) -> None:
        factory = self.session_factory
        if factory is None:
            from browser_use import BrowserSession  # type: ignore[import-not-found]

            factory = BrowserSession
        session = factory(cdp_url=self.cdp_url, keep_alive=True)
        try:
            await session.start()
        finally:
            await session.stop()

    def recover(self, request: RecoveryRequest) -> Sequence[TraceAction]:
        # The Browser Use attachment is local; only RecoveryRequest reaches planner.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._verify_attachment())
        else:
            raise RuntimeError("sync recovery cannot run inside an active event loop")
        return tuple(self.planner(request))
