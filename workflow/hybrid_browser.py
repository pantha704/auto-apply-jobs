from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import nullcontext
from dataclasses import dataclass

from .adapters.base import BrowserDriver, DeterministicAdapter, DriverAction, RuntimeContext
from .recovery import ActionRisk, RecoveryRequest, TraceAction, sanitized_request


@dataclass(frozen=True)
class HybridResult:
    ok: bool
    route: str
    reason: str = ""


class HybridBrowserRuntime:
    """Route known intents through Playwright and use Browser Use only to recover drift.

    The recovery provider receives a sanitized candidate inventory. Its proposal is
    revalidated against the fresh inventory and replayed by the deterministic driver;
    Browser Use never owns submission truth and can never execute terminal actions.
    """

    def __init__(
        self,
        driver: BrowserDriver,
        *,
        recovery_provider=None,
        lease=None,
        adapter_resolver: Callable[[str, str], DeterministicAdapter | None] | None = None,
    ) -> None:
        self.driver = driver
        self.recovery_provider = recovery_provider
        self.lease = lease
        self.adapter_resolver = adapter_resolver

    def run(self, context: RuntimeContext, portal: str, intent: str) -> HybridResult:
        lease = self.lease.acquire(context.session_id) if self.lease else nullcontext()
        with lease:
            candidates = tuple(self.driver.inspect(context.url))
            adapter = self.adapter_resolver(portal, intent) if self.adapter_resolver else None
            if adapter is not None:
                actions = tuple(adapter.plan(candidates, context))
                if actions and self._safe_replay(actions, candidates) and self.driver.replay(actions):
                    return HybridResult(True, "playwright")

            if self.recovery_provider is None:
                return HybridResult(False, "none", "no deterministic recipe or recovery provider")
            request = sanitized_request(candidates, site_id=portal, intent=intent)
            if not request.candidates:
                return HybridResult(False, "browser_use", "no safe actionable candidates")
            trace = tuple(self.recovery_provider.recover(request))
            if len(trace) != 1:
                return HybridResult(False, "browser_use", "ambiguous recovery proposal")
            action = trace[0]
            candidate = next((c for c in request.candidates if c.candidate_id == action.candidate_id), None)
            if candidate is None or candidate.role != action.target_role:
                return HybridResult(False, "browser_use", "proposal is not in fresh inventory")
            if action.risk_for(candidate) is not ActionRisk.LOW:
                return HybridResult(False, "browser_use", "recovery cannot execute form or terminal actions")
            if not self.driver.replay((DriverAction(action.action_type, action.intent, action.candidate_id),)):
                return HybridResult(False, "playwright", "recovered action replay failed")
            return HybridResult(True, "browser_use->playwright")

    @staticmethod
    def _safe_replay(actions: Sequence[DriverAction], candidates) -> bool:
        by_id = {item.candidate_id: item for item in candidates}
        for action in actions:
            candidate = by_id.get(action.target_id)
            if candidate is None or action.intent.casefold() in {"submit", "send", "final_submit", "submit_application"}:
                return False
            if action.action_type not in {"click", "check", "fill", "select"}:
                return False
            if action.action_type != "click" or candidate.risk is not ActionRisk.TERMINAL:
                continue
            return False
        return True
