from __future__ import annotations

import os
import sqlite3
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from cryptography.fernet import Fernet

from .portal_session_runtime import PortalSessionUnavailable, current_session
from .preferences import PreferenceEvaluation, PreferenceRepository, PreferenceSet, evaluate_preferences, extract_monthly_pay_inr
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
    *,
    lease_seconds: int = 600,
) -> int:
    """Claim one already-selected job and atomically persist immutable pins.

    F03 fix: Creates a durable lease with:
    - Timed lease expiry (default 10 minutes)
    - Random fencing token (prevents stale-worker writes)
    - Attempt count increment
    - Claim timestamp
    - Owner token for conditional mutations

    R5: caller must retain fence via fence_token_for() and pass it to
    guarded transitions. finalize/mark paths must verify owner+fence.
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    lease_expires = (now + timedelta(seconds=lease_seconds)).isoformat()
    fence_token = secrets.token_urlsafe(32)

    updated = db.execute(
        """UPDATE jobs SET
           status='claimed',
           claimed_by=?,
           claimed_at=?,
           lease_expires_at=?,
           attempt_count=COALESCE(attempt_count, 0) + 1,
           last_fence_token=?,
           candidate_profile_id=?,
           candidate_profile_revision=?,
           resume_version_id=?,
           preference_set_id=?,
           preference_set_version=?,
           portal_session_revision=?
           WHERE id=? AND (status='pending' OR
             (status='claimed' AND lease_expires_at < ?))""",
        (
            worker_id,
            now_iso,
            lease_expires,
            fence_token,
            runtime.profile_id,
            runtime.profile_revision,
            runtime.resume_id,
            runtime.preference_set.id,
            runtime.preference_set.version,
            runtime.session_revision,
            job_id,
            now_iso,
        ),
    )
    return updated.rowcount


def fence_token_for(db: sqlite3.Connection, job_id: str, worker_id: str) -> str | None:
    """Return current fence token for owner, else None. R5 enforcement helper."""
    row = db.execute(
        "SELECT last_fence_token FROM jobs WHERE id=? AND claimed_by=?",
        (job_id, worker_id),
    ).fetchone()
    return row[0] if row and row[0] else None


def check_fence(db: sqlite3.Connection, job_id: str, worker_id: str, fence_token: str | None) -> bool:
    """True iff job still owned by worker with matching fence and live lease."""
    if not fence_token:
        return False
    row = db.execute(
        "SELECT claimed_by, last_fence_token, lease_expires_at, status FROM jobs WHERE id=?",
        (job_id,),
    ).fetchone()
    if not row:
        return False
    owner, token, lease_exp, status = row
    if owner != worker_id or token != fence_token:
        return False
    if status != "claimed":
        return False
    if lease_exp:
        try:
            from datetime import datetime as _dt

            if _dt.fromisoformat(lease_exp) < _dt.now(timezone.utc):
                return False
        except ValueError:
            return False
    return True


def guarded_mark(
    db: sqlite3.Connection,
    job_id: str,
    worker_id: str,
    status: str,
    result: str = "",
    fence_token: str | None = None,
) -> int:
    """Owner-checked transition. Returns rowcount (1 ok, 0 lost ownership).

    R5: all worker mark() paths must go through here. If fence_token given,
    it must match; otherwise ownership alone is required. Uncertain post-send
    outcomes can never return to pending.
    """
    result = (result or "")[:200]
    if "submit-unconfirmed" in result or "send-unconfirmed" in result:
        if status == "pending":
            raise ValueError("guarded_mark refused: uncertain post-send cannot requeue")
    if fence_token:
        cur = db.execute(
            "UPDATE jobs SET status=?, result=? WHERE id=? AND claimed_by=? AND last_fence_token=?",
            (status, result, job_id, worker_id, fence_token),
        )
        return cur.rowcount
    cur = db.execute(
        "UPDATE jobs SET status=?, result=? WHERE id=? AND claimed_by=?",
        (status, result, job_id, worker_id),
    )
    return cur.rowcount


def eligible_for_claim(runtime: PublishedApplicationRuntime, job: Mapping[str, Any]) -> bool:
    """Evaluate against full normalized job facts — title, portal, location,
    company, compensation, and description length — not just title+portal.

    Workers MUST pass the full job row (or a dict containing at least title,
    portal, location, and company) so the policy engine can evaluate all active
    rules.  Missing fields are set to safe defaults that let the policy's
    unknown_policy controls take effect.
    """
    enriched = dict(job)
    # Ensure salary_min is populated from title extraction if missing.
    if enriched.get("salary_min") in (None, ""):
        pay = extract_monthly_pay_inr(str(enriched.get("title") or ""))
        if pay is not None:
            enriched["salary_min"] = pay
    # Ensure location is available for policy checks.
    if not enriched.get("location") and not enriched.get("location_raw"):
        enriched["location"] = ""
    # Ensure description_length is populated from description if present.
    desc = enriched.get("description") or ""
    if not enriched.get("description_length"):
        enriched["description_length"] = len(desc)
    result = runtime.evaluate(enriched)
    return result.eligible and not result.needs_review
