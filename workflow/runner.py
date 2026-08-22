from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Protocol, runtime_checkable

from .adapters.base import (
    BrowserDriver,
    DeterministicAdapter,
    DriverAction,
    RuntimeContext,
    SessionLease,
)
from .providers import RecoveryProvider
from .recovery import ActionRisk, TraceAction, sanitized_request


class Route(StrEnum):
    RECIPE = "recipe"
    DETERMINISTIC = "deterministic"
    RECOVERY = "recovery"
    RECOVERY_SHADOW = "recovery_shadow"
    OPERATOR = "operator"


@dataclass(frozen=True)
class RoutingResult:
    route: Route
    trace: tuple[TraceAction, ...] = ()
    review_reason: str = ""

    @property
    def review_required(self) -> bool:
        return self.route is Route.OPERATOR


@runtime_checkable
class VerifiedRecipe(Protocol):
    verified: bool

    def replay(self, driver: BrowserDriver, context: RuntimeContext) -> bool: ...


class RuntimeRouter:
    """Fail-closed route selection with one lease around all browser access."""

    def __init__(
        self,
        driver: BrowserDriver,
        lease: SessionLease,
        adapter: DeterministicAdapter,
        recovery_provider: RecoveryProvider | None = None,
        recovery_mode: str = "execute",
        recovery_verifier: Callable[[RuntimeContext, tuple[TraceAction, ...]], bool] | None = None,
        verified_learner: Callable[[RuntimeContext, tuple[TraceAction, ...]], None] | None = None,
    ) -> None:
        self.driver = driver
        self.lease = lease
        self.adapter = adapter
        self.recovery_provider = recovery_provider
        self.recovery_verifier = recovery_verifier
        self.verified_learner = verified_learner
        if recovery_mode not in {"disabled", "shadow", "execute"}:
            raise ValueError("recovery_mode must be disabled, shadow, or execute")
        self.recovery_mode = recovery_mode

    def run(
        self, context: RuntimeContext, *, recipe: VerifiedRecipe | None = None,
        recovery_intent: str = "recover_navigation",
    ) -> RoutingResult:
        with self.lease.acquire(context.session_id):
            if recipe is not None and recipe.verified:
                if recipe.replay(self.driver, context):
                    return RoutingResult(Route.RECIPE)

            candidates = tuple(self.driver.inspect(context.url))
            deterministic = self.adapter.plan(candidates, context)
            deterministic_candidates = {item.candidate_id: item for item in candidates}
            if deterministic and any(
                action.target_id not in deterministic_candidates
                or TraceAction(
                    action.action_type,
                    action.intent,
                    action.target_id,
                    deterministic_candidates[action.target_id].role,
                ).risk_for(deterministic_candidates[action.target_id]) is ActionRisk.TERMINAL
                for action in deterministic
            ):
                return RoutingResult(Route.OPERATOR, review_reason="unsafe deterministic plan")
            if deterministic and self.driver.replay(deterministic):
                if self.recovery_verifier is not None:
                    try:
                        if not self.recovery_verifier(context, ()):
                            return RoutingResult(
                                Route.OPERATOR,
                                review_reason="deterministic postcondition failed",
                            )
                    except Exception:
                        return RoutingResult(
                            Route.OPERATOR,
                            review_reason="deterministic postcondition failed",
                        )
                return RoutingResult(Route.DETERMINISTIC)

            if self.recovery_provider is None or self.recovery_mode == "disabled":
                return RoutingResult(Route.OPERATOR, review_reason="recovery unavailable")

            request = sanitized_request(
                candidates,
                site_id=context.session_id.split(":", 1)[0],
                intent=recovery_intent,
            )
            if not request.candidates:
                return RoutingResult(Route.OPERATOR, review_reason="no actionable candidates")

            try:
                trace = tuple(self.recovery_provider.recover(request))
            except Exception:
                return RoutingResult(Route.OPERATOR, review_reason="recovery failed")

            if not trace:
                return RoutingResult(Route.OPERATOR, review_reason="recovery produced no plan")
            candidate_map = {item.candidate_id: item for item in request.candidates}
            if any(
                action.candidate_id not in candidate_map
                or candidate_map[action.candidate_id].role != action.target_role
                or action.risk_for(candidate_map[action.candidate_id]) is not ActionRisk.LOW
                or (recovery_intent != "recover_navigation" and action.intent != recovery_intent)
                for action in trace
            ):
                return RoutingResult(Route.OPERATOR, review_reason="unsafe recovery plan")

            if self.recovery_mode == "shadow":
                return RoutingResult(
                    Route.RECOVERY_SHADOW,
                    trace=trace,
                    review_reason="recovery shadow proposal",
                )

            current = tuple(self.driver.inspect(context.url))
            current_request = sanitized_request(
                current,
                site_id=request.site_id,
                intent=request.intent,
            )
            if current_request.page_fingerprint != request.page_fingerprint:
                return RoutingResult(Route.OPERATOR, review_reason="stale recovery observation")
            if self.recovery_verifier is None:
                return RoutingResult(
                    Route.OPERATOR,
                    trace=trace,
                    review_reason="recovery postcondition unavailable",
                )

            replay = tuple(
                DriverAction(action.action_type, action.intent, action.candidate_id)
                for action in trace
            )
            if not self.driver.replay(replay):
                return RoutingResult(Route.OPERATOR, review_reason="recovery replay failed")
            try:
                verified = self.recovery_verifier(context, trace)
            except Exception:
                verified = False
            if not verified:
                return RoutingResult(
                    Route.OPERATOR,
                    trace=trace,
                    review_reason="recovery postcondition failed",
                )
            if self.verified_learner is not None:
                try:
                    self.verified_learner(context, trace)
                except Exception:
                    pass
            return RoutingResult(Route.RECOVERY, trace=trace)
