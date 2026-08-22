from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re

_ALLOWED_ROLES = frozenset({"button", "field", "checkbox", "select", "link", "text"})
_ACTIONABLE_ROLES = frozenset({"button", "field", "checkbox", "select"})
_FORBIDDEN_LABEL = re.compile(
    r"<[^>]+>|(?:cookie|authorization|password|secret|token|value)\s*[:=]|"
    r"\bauthorization\s+bearer\s+[A-Za-z0-9._~-]{8,}|"
    r"\bbearer\s+[A-Za-z0-9._~-]{8,}|https?://|"
    r"(?:\+?\d[\d\s().-]{8,}\d)|\b(?:otp|one[- ]time)\s*[:#-]?\s*\d{4,8}\b|"
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)
_TERMINAL_LABEL = re.compile(
    r"\b(?:submit|send|finali[sz]e|confirm)\b.*\b(?:application|message|email)?\b|"
    r"\b(?:application|message|email)\b.*\b(?:submit|send|finali[sz]e|confirm)\b|"
    r"\bapply\s+now\s+final\b",
    re.IGNORECASE,
)
_TERMINAL_INTENT = re.compile(
    r"(?:^|_)(?:submit|send|confirm|finali[sz]e)(?:_|$)|apply_now_final|complete_application",
    re.IGNORECASE,
)


class ActionRisk(StrEnum):
    LOW = "low"
    FORM_INPUT = "form_input"
    TERMINAL = "terminal"


@dataclass(frozen=True)
class ActionCandidate:
    """Sanitized page affordance. It intentionally has no value/payload field."""

    candidate_id: str
    role: str
    label: str

    def __post_init__(self) -> None:
        if not self.candidate_id or self.role not in _ALLOWED_ROLES:
            raise ValueError("candidate requires an id and supported role")
        if not self.label.strip() or _FORBIDDEN_LABEL.search(self.label):
            raise ValueError("candidate label contains sensitive or non-semantic material")
        if len(self.label) > 160:
            raise ValueError("candidate label is too long")

    @property
    def actionable(self) -> bool:
        return self.role in _ACTIONABLE_ROLES

    @property
    def risk(self) -> ActionRisk:
        if _TERMINAL_LABEL.search(self.label):
            return ActionRisk.TERMINAL
        if self.role in {"field", "checkbox", "select"}:
            return ActionRisk.FORM_INPUT
        return ActionRisk.LOW


@dataclass(frozen=True)
class RecoveryRequest:
    """The complete and deliberately narrow recovery-provider input."""

    candidates: tuple[ActionCandidate, ...]
    site_id: str = "unknown"
    intent: str = "recover_navigation"
    page_fingerprint: str = ""
    allowed_risk: ActionRisk = ActionRisk.LOW

    def __post_init__(self) -> None:
        if any(not candidate.actionable for candidate in self.candidates):
            raise ValueError("recovery candidates must be actionable")
        if not self.site_id or not self.intent:
            raise ValueError("recovery request requires site_id and intent")


@dataclass(frozen=True)
class TraceAction:
    action_type: str
    intent: str
    candidate_id: str
    target_role: str

    def __post_init__(self) -> None:
        if self.action_type not in {"click", "fill", "check", "select"}:
            raise ValueError("unsupported recovery action")
        if not all((self.intent, self.candidate_id, self.target_role)):
            raise ValueError("typed recovery action fields are required")

    @property
    def is_final_submit(self) -> bool:
        return bool(_TERMINAL_INTENT.search(self.intent))

    def risk_for(self, candidate: ActionCandidate) -> ActionRisk:
        if self.is_final_submit or candidate.risk is ActionRisk.TERMINAL:
            return ActionRisk.TERMINAL
        if self.action_type in {"fill", "check", "select"} or candidate.risk is ActionRisk.FORM_INPUT:
            return ActionRisk.FORM_INPUT
        return ActionRisk.LOW


def page_fingerprint(candidates) -> str:
    payload = [
        {"id": item.candidate_id, "role": item.role, "label": item.label}
        for item in candidates
    ]
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def sanitized_request(
    candidates,
    *,
    site_id: str = "unknown",
    intent: str = "recover_navigation",
) -> RecoveryRequest:
    safe = tuple(candidate for candidate in candidates if candidate.actionable)
    return RecoveryRequest(
        safe,
        site_id=site_id,
        intent=intent,
        page_fingerprint=page_fingerprint(safe),
    )
