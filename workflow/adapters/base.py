from __future__ import annotations

from dataclasses import dataclass
from typing import ContextManager, Protocol, Sequence, runtime_checkable

from workflow.recovery import ActionCandidate


@dataclass(frozen=True)
class RuntimeContext:
    """Execution context retained by the deterministic runtime, never recovery."""

    session_id: str
    url: str

    def __post_init__(self) -> None:
        if not self.session_id or not self.url:
            raise ValueError("session_id and url are required")


@dataclass(frozen=True)
class DriverAction:
    """A replayable action addressed by a stable page element identifier."""

    action_type: str
    intent: str
    target_id: str


@runtime_checkable
class BrowserDriver(Protocol):
    """Abstract deterministic replay boundary for CDP-backed browser drivers."""

    def inspect(self, url: str) -> Sequence[ActionCandidate]: ...

    def replay(self, actions: Sequence[DriverAction]) -> bool: ...


@runtime_checkable
class SessionLease(Protocol):
    """Serializes all inspection and mutation of one browser session."""

    def acquire(self, session_id: str) -> ContextManager[None]: ...


@runtime_checkable
class DeterministicAdapter(Protocol):
    name: str

    def plan(
        self, candidates: Sequence[ActionCandidate], context: RuntimeContext
    ) -> tuple[DriverAction, ...]: ...
