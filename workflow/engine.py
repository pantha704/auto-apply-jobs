from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable


class State(StrEnum):
    INIT = "init"
    PROFILE_READY = "profile_ready"
    SESSION_READY = "session_ready"
    JOB_LOADED = "job_loaded"
    FORM_READY = "form_ready"
    SUBMIT_READY = "submit_ready"
    SUBMITTING = "submitting"
    REVIEW_REQUIRED = "review_required"
    CONFIRMED = "confirmed"
    FAILED = "failed"


class Event(StrEnum):
    PROFILE_APPROVED = "profile_approved"
    SESSION_ACQUIRED = "session_acquired"
    JOB_OPENED = "job_opened"
    FORM_DISCOVERED = "form_discovered"
    FORM_COMPLETED = "form_completed"
    UNKNOWN_QUESTION = "unknown_question"
    SUBMIT_AUTHORIZED = "submit_authorized"
    CONFIRMATION_OBSERVED = "confirmation_observed"
    FAILURE = "failure"


class InvalidTransition(RuntimeError):
    pass


_TRANSITIONS = {
    (State.INIT, Event.PROFILE_APPROVED): State.PROFILE_READY,
    (State.PROFILE_READY, Event.SESSION_ACQUIRED): State.SESSION_READY,
    (State.SESSION_READY, Event.JOB_OPENED): State.JOB_LOADED,
    (State.JOB_LOADED, Event.FORM_DISCOVERED): State.FORM_READY,
    (State.FORM_READY, Event.FORM_COMPLETED): State.SUBMIT_READY,
    (State.FORM_READY, Event.UNKNOWN_QUESTION): State.REVIEW_REQUIRED,
    (State.SUBMIT_READY, Event.SUBMIT_AUTHORIZED): State.SUBMITTING,
    (State.SUBMITTING, Event.CONFIRMATION_OBSERVED): State.CONFIRMED,
}


@dataclass
class StateMachine:
    state: State = State.INIT
    history: list[tuple[State, Event, State]] = field(default_factory=list)

    def apply(self, event: Event) -> State:
        if event is Event.FAILURE and self.state not in {
            State.CONFIRMED,
            State.FAILED,
        }:
            target = State.FAILED
        else:
            target = _TRANSITIONS.get((self.state, event))
        if target is None:
            raise InvalidTransition(f"{event.value} is invalid from {self.state.value}")
        before = self.state
        self.state = target
        self.history.append((before, event, target))
        return target


@runtime_checkable
class AtsAdapter(Protocol):
    """Portal-neutral boundary. Implementations return typed observations only."""

    name: str

    def can_handle(self, url: str) -> bool: ...

    def discover_form(self) -> tuple[str, ...]: ...

    def fill_field(self, intent: str, value: str) -> None: ...

    def authorize_submit(self) -> None: ...

    def observe_submission(self) -> object: ...
