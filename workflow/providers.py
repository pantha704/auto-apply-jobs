from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from .recovery import RecoveryRequest, TraceAction


@runtime_checkable
class RecoveryProvider(Protocol):
    """Optional planner only; providers neither receive values nor drive browsers."""

    def recover(self, request: RecoveryRequest) -> Sequence[TraceAction]: ...
