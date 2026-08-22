from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class OutcomeCode(StrEnum):
    SUBMITTED = "submitted"
    ALREADY_APPLIED = "already_applied"
    CLOSED = "closed"
    NOT_QUALIFIED = "not_qualified"
    UNKNOWN_ANSWER = "unknown_answer"
    AUTH_REQUIRED = "auth_required"
    UI_DRIFT = "ui_drift"
    CONFIRMATION_AMBIGUOUS = "confirmation_ambiguous"
    TRANSIENT_ERROR = "transient_error"
    PERMANENT_ERROR = "permanent_error"


@dataclass(frozen=True)
class Outcome:
    code: OutcomeCode
    confirmed: bool
    retryable: bool
    safe_detail: str = ""

    def __post_init__(self) -> None:
        if self.confirmed != (self.code is OutcomeCode.SUBMITTED):
            raise ValueError("only submitted outcomes may be confirmed")
        if self.confirmed and self.retryable:
            raise ValueError("confirmed outcomes cannot be retryable")


@dataclass(frozen=True)
class SubmissionEvidence:
    observed_at: str
    success_text: str | None = None
    application_id: str | None = None
    artifact_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.observed_at:
            raise ValueError("submission evidence requires observed_at")
        if not self.success_text and not self.application_id:
            raise ValueError("submission evidence requires a deterministic success signal")


@dataclass(frozen=True)
class Claim:
    job_id: str
    run_id: str
    worker_id: str
    portal: str
    url: str
    title: str
    attempt_count: int
    lease_token: str
    lease_expires_at: str
