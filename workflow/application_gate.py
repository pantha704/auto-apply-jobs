from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from cryptography.fernet import Fernet

from .portal_session_runtime import PortalSessionUnavailable, current_session
from .preferences import PreferenceEvaluation, PreferenceRepository, PreferenceSet, evaluate_preferences
from .profile_service import ProfileService

ROOT = Path(__file__).resolve().parents[1]


class PublicationUnavailable(RuntimeError):
    """A complete immutable applicant publication is not available."""


@dataclass(frozen=True)
class PublishedApplicationRuntime:
    profile_id: str
    profile_revision: int
    resume_id: str
    resume_path: str
    preference_set: PreferenceSet
    session_revision: int
    facts: Mapping[str, Any]

    def fact(self, *aliases: str) -> Any:
        for key in aliases:
            value = self.facts.get(key)
            if value is not None and (not isinstance(value, str) or value.strip()):
                return value
        raise PublicationUnavailable(f"published profile is missing required fact: {aliases[0]}")

    def boolean_fact(self, *aliases: str) -> bool:
        value = self.fact(*aliases)
        if not isinstance(value, bool):
            raise PublicationUnavailable(
                f"published profile fact must be boolean: {aliases[0]}"
            )
        return value

    def evaluate(self, job: Mapping[str, Any]) -> PreferenceEvaluation:
        return evaluate_preferences(job, self.preference_set)


def _service() -> tuple[ProfileService, PreferenceRepository]:
    control_db = Path(os.getenv("JOBHUNT_CONTROL_DB", ROOT / "controlplane.db"))
    key_path = Path(os.getenv("JOBHUNT_VAULT_KEY", ROOT / ".controlplane.key"))
    resume_storage = Path(
        os.getenv("JOBHUNT_RESUME_STORAGE", ROOT / ".private" / "resumes")
    )
    try:
        key = key_path.read_bytes().strip()
        fernet = Fernet(key)
    except (OSError, ValueError) as exc:
        raise PublicationUnavailable("profile vault is unavailable") from exc
    return ProfileService(control_db, resume_storage, fernet), PreferenceRepository(control_db)


def published_runtime(
    portal: str | None,
    required_fact_groups: Iterable[Iterable[str]] = (),
    *,
    require_session: bool = True,
) -> PublishedApplicationRuntime:
    profiles, preferences = _service()
    readiness = profiles.readiness_summary()
    if not readiness["ready"]:
        raise PublicationUnavailable("approved profile and resume are required")
    profile = profiles.get_profile(readiness["approved_profile_id"])
    resume = profiles.get_resume(readiness["approved_resume_id"])
    policy = preferences.get_active()
    if policy is None or not policy.rules:
        raise PublicationUnavailable("a non-empty active policy is required")
    if require_session:
        if portal is None:
            raise PublicationUnavailable("a portal is required for session gating")
        try:
            session_revision = current_session(portal).revision
        except PortalSessionUnavailable as exc:
            raise PublicationUnavailable(f"{portal} session is not valid") from exc
    else:
        session_revision = 0
    runtime = PublishedApplicationRuntime(
        profile_id=str(profile["id"]),
        profile_revision=int(profile["revision"]),
        resume_id=str(resume["id"]),
        resume_path=str(resume["path"]),
        preference_set=policy,
        session_revision=session_revision,
        facts=dict(profile["facts"]),
    )
    for aliases in required_fact_groups:
        runtime.fact(*tuple(aliases))
    return runtime


def pin_claim(
    db: sqlite3.Connection,
    job_id: str,
    worker_id: str,
    runtime: PublishedApplicationRuntime,
) -> int:
    """Claim one already-selected job and atomically persist immutable pins."""
    updated = db.execute(
        """UPDATE jobs SET status='claimed',claimed_by=?,candidate_profile_id=?,
           candidate_profile_revision=?,resume_version_id=?,preference_set_id=?,
           preference_set_version=?,portal_session_revision=?
           WHERE id=? AND status='pending'""",
        (
            worker_id,
            runtime.profile_id,
            runtime.profile_revision,
            runtime.resume_id,
            runtime.preference_set.id,
            runtime.preference_set.version,
            runtime.session_revision,
            job_id,
        ),
    )
    return updated.rowcount


def eligible_for_claim(runtime: PublishedApplicationRuntime, job: Mapping[str, Any]) -> bool:
    result = runtime.evaluate(job)
    return result.eligible and not result.needs_review
