from __future__ import annotations

from collections.abc import Sequence

from .base import DriverAction, RuntimeContext
from workflow.recovery import ActionCandidate


class GenericDeterministicAdapter:
    """Small portal-neutral adapter driven only by semantic labels."""

    name = "generic"

    _FIELD_INTENTS = {
        "email": "email",
        "email address": "email",
        "first name": "first_name",
        "last name": "last_name",
        "phone": "phone",
        "phone number": "phone",
    }

    def plan(
        self, candidates: Sequence[ActionCandidate], context: RuntimeContext
    ) -> tuple[DriverAction, ...]:
        del context
        actions: list[DriverAction] = []
        for candidate in candidates:
            if candidate.role != "field":
                continue
            intent = self._FIELD_INTENTS.get(candidate.label.strip().casefold())
            if intent:
                actions.append(DriverAction("fill", intent, candidate.candidate_id))
        return tuple(actions)
