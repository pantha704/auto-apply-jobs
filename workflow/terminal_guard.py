"""Terminal action guard — one gateway for all submit/finalize actions.

F08 fix: All recovery and deterministic routes pass through this guard
before any terminal action (submit, send, finalize) executes. Validates
intent + label + context + role match before allowing execution.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from .recovery import ActionCandidate, ActionRisk, TraceAction

_TERMINAL_INTENTS = frozenset({"submit", "send", "finalize", "confirm"})
_TERMINAL_LABEL = re.compile(
    r"\b(?:submit|send|finali[sz]e|confirm)\b.*\b(?:application|message|email)?\b|"
    r"\b(?:application|message|email)\b.*\b(?:submit|send|finali[sz]e|confirm)\b|"
    r"\bapply\s+now\s+final\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TerminalActionRequest:
    """Request to execute a terminal action."""
    intent: str
    candidate_id: str
    label: str
    role: str
    portal: str
    session_id: str


@dataclass(frozen=True)
class TerminalActionResult:
    allowed: bool
    reason: str = ""


class TerminalActionGuard:
    """Validates intent + label + context + role before any terminal action.

    All recovery modes (execute, shadow) and deterministic routes pass through
    this guard. It ensures:
    1. The intent is actually terminal
    2. The label matches terminal patterns
    3. The role is compatible (button/link)
    4. The portal and session context match
    """
    
    def __init__(
        self,
        allowed_portals: frozenset[str] | None = None,
        allowed_roles: frozenset[str] = frozenset({"button", "link"}),
    ) -> None:
        self.allowed_portals = allowed_portals
        self.allowed_roles = allowed_roles

    def validate(
        self,
        request: TerminalActionRequest,
        candidates: Sequence[ActionCandidate],
    ) -> TerminalActionResult:
        """Validate a terminal action request against candidates and context."""
        # 1. Intent must be terminal
        if request.intent.lower() not in _TERMINAL_INTENTS:
            return TerminalActionResult(
                False, f"intent '{request.intent}' is not a terminal action"
            )

        # 2. Label must match terminal patterns
        if not _TERMINAL_LABEL.search(request.label):
            return TerminalActionResult(
                False, f"label '{request.label[:50]}' does not match terminal pattern"
            )

        # 3. Role must be compatible
        if request.role.lower() not in self.allowed_roles:
            return TerminalActionResult(
                False, f"role '{request.role}' is not allowed for terminal actions"
            )

        # 4. Portal must be in allowed set (if configured)
        if self.allowed_portals and request.portal.lower() not in self.allowed_portals:
            return TerminalActionResult(
                False, f"portal '{request.portal}' is not in allowed set"
            )

        # 5. Candidate must exist and match label + role
        candidate = None
        for c in candidates:
            if c.candidate_id == request.candidate_id:
                candidate = c
                break
        
        if candidate is None:
            return TerminalActionResult(
                False, f"candidate '{request.candidate_id}' not found in inventory"
            )

        # 6. Candidate label must match request label
        if candidate.label.casefold() != request.label.casefold():
            return TerminalActionResult(
                False, f"candidate label '{candidate.label[:50]}' does not match request label"
            )

        # 7. Candidate role must match request role
        if candidate.role.lower() != request.role.lower():
            return TerminalActionResult(
                False, f"candidate role '{candidate.role}' does not match request role"
            )

        # 8. Candidate must be terminal risk
        if candidate.risk is not ActionRisk.TERMINAL:
            return TerminalActionResult(
                False, f"candidate risk '{candidate.risk}' is not terminal"
            )

        return TerminalActionResult(True)

    def validate_trace(
        self,
        trace: Sequence[TraceAction],
        candidates: Sequence[ActionCandidate],
        portal: str,
        session_id: str,
    ) -> TerminalActionResult:
        """Validate a trace that may contain terminal actions."""
        for action in trace:
            if action.is_final_submit:
                candidate = None
                for c in candidates:
                    if c.candidate_id == action.candidate_id:
                        candidate = c
                        break
                
                if candidate is None:
                    return TerminalActionResult(
                        False, f"trace candidate '{action.candidate_id}' not found"
                    )

                request = TerminalActionRequest(
                    intent=action.intent,
                    candidate_id=action.candidate_id,
                    label=candidate.label,
                    role=action.target_role,
                    portal=portal,
                    session_id=session_id,
                )
                result = self.validate(request, candidates)
                if not result.allowed:
                    return result

        return TerminalActionResult(True)
