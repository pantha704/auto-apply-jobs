from __future__ import annotations

import os
import sqlite3
import secrets
import json
import uuid
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
    cursor=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,))
    values=cursor.fetchone()
    if values is None:
        return 0
    facts=dict(zip([column[0] for column in cursor.description],values))
    if facts.get('normalized_payload'):
        try:
            normalized=json.loads(facts['normalized_payload'])
            if not isinstance(normalized,dict):
                raise ValueError('invalid job payload')
            facts.update(normalized)
        except (ValueError,TypeError):
            db.execute("UPDATE jobs SET status='skip',result='needs-review:invalid-job-payload' WHERE id=? AND status='pending'",(job_id,))
            return 0
    if hasattr(runtime.preference_set,'rules') and not eligible_for_claim(runtime,facts):
        db.execute("UPDATE jobs SET status='skip',result='needs-review:policy',last_outcome_code='needs-review:policy' WHERE id=? AND status='pending'",(job_id,))
        return 0
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
             (status='claimed' AND lease_expires_at < ?))
           AND (eligibility IS NULL OR eligibility=1)
           AND NOT EXISTS (SELECT 1 FROM submission_intents WHERE job_id=jobs.id)""",
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
    if updated.rowcount:
        from .release_bundle import build_from_runtime
        row = db.execute("SELECT portal,url,attempt_count FROM jobs WHERE id=?", (job_id,)).fetchone()
        database = next(r[2] for r in db.execute("PRAGMA database_list") if r[1]=='main')
        db.execute("UPDATE application_runs SET state='failed',finished_at=?,outcome_code='lease_expired' WHERE job_id=? AND state='running'", (now_iso,job_id))
        db.execute("UPDATE job_attempts SET finished_at=?,outcome_code='lease_expired' WHERE run_id IN (SELECT id FROM application_runs WHERE job_id=? AND outcome_code='lease_expired') AND finished_at IS NULL", (now_iso,job_id))
        run_id = uuid.uuid4().hex
        db.execute("""INSERT INTO application_runs
            (id,job_id,adapter,lease_token,worker_id,state,started_at,candidate_profile_id,
             resume_version_id,preference_set_id,portal_session_revision)
            VALUES(?,?,?,?,?,'running',?,?,?,?,?)""",
            (run_id,job_id,row[0],fence_token,worker_id,now_iso,runtime.profile_id,
             runtime.resume_id,runtime.preference_set.id,runtime.session_revision))
        db.execute("INSERT INTO job_attempts(run_id,attempt_no,started_at) VALUES(?,?,?)", (run_id,row[2],now_iso))
        remember_claim(job_id,fence_token=fence_token,
                       bundle=build_from_runtime(row[0],runtime,job_id,fence_token),
                       database=database,worker_id=worker_id,portal=row[0],url=row[1],run_id=run_id)
    return updated.rowcount


def fence_token_for(db: sqlite3.Connection, job_id: str, worker_id: str) -> str | None:
    """Return current fence token for owner, else None. R5 enforcement helper."""
    row = db.execute(
        "SELECT last_fence_token FROM jobs WHERE id=? AND claimed_by=?",
        (job_id, worker_id),
    ).fetchone()
    return row[0] if row and row[0] else None


# B3/B6: in-memory claim registry. Workers record the fence token and release
# bundle at claim time; mark()/finalize paths consume them without threading
# new arguments through every call site. The DB remains authoritative —
# guarded_mark/finalize re-validate against stored state.
_CLAIM_REGISTRY: dict[str, dict] = {}
_ACTIVE_JOB = None


def active_claim():
    return _CLAIM_REGISTRY.get(_ACTIVE_JOB)



def remember_claim(job_id: str | int, *, fence_token: str = "", bundle=None, **context) -> None:
    """Record claim-time fence + bundle for this process's owned job."""
    global _ACTIVE_JOB
    _ACTIVE_JOB = str(job_id)
    _CLAIM_REGISTRY[str(job_id)] = {"job_id": str(job_id),"fence_token": fence_token or "","bundle": bundle,**context}


def claim_fence(job_id: str | int) -> str:
    """Fence token recorded at claim time, or empty string."""
    return _CLAIM_REGISTRY.get(str(job_id), {}).get("fence_token") or ""


def claim_bundle(job_id: str | int):
    """Release bundle recorded at claim time, or None."""
    return _CLAIM_REGISTRY.get(str(job_id), {}).get("bundle")


def forget_claim(job_id: str | int) -> None:
    """Drop claim-time state after the job reaches a terminal outcome."""
    _CLAIM_REGISTRY.pop(str(job_id), None)


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
    if not lease_exp:
        return False
    try:
        expiry = datetime.fromisoformat(lease_exp)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return expiry > datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return False


def guarded_mark(
    db: sqlite3.Connection,
    job_id: str,
    worker_id: str,
    status: str,
    result: str = "",
    fence_token: str | None = None,
) -> int:
    """Complete only the original owner's claim; never turn a lost response into a retry."""
    row = db.execute("SELECT status,claimed_by,last_fence_token,result,last_outcome_code FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row or row[1]!=worker_id or (row[2] and row[2]!=fence_token):
        return 0
    if status=='pending' and any(x in f"{row[3]} {row[4]} {result}" for x in ('submitted','applied','unconfirmed','unknown-outcome')):
        raise ValueError('guarded_mark refused: stored post-send history cannot requeue')
    if row[0]!='claimed':
        return 0
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'submission_intents' in tables and db.execute("SELECT 1 FROM submission_intents WHERE job_id=?", (job_id,)).fetchone():
        status,result='skip','unknown-outcome'
    cur = db.execute("UPDATE jobs SET status=?,result=?,last_outcome_code=? WHERE id=? AND status='claimed' AND claimed_by=? AND last_fence_token IS ?", (status,result[:200],result[:80],job_id,worker_id,row[2]))
    if cur.rowcount and 'application_runs' in tables:
        now = datetime.now(timezone.utc).isoformat()
        db.execute("UPDATE application_runs SET state='finished',finished_at=?,outcome_code=? WHERE job_id=? AND lease_token=? AND state='running'", (now,result[:80],job_id,row[2]))
        db.execute("UPDATE job_attempts SET finished_at=?,outcome_code=? WHERE run_id IN (SELECT id FROM application_runs WHERE job_id=? AND lease_token=?) AND finished_at IS NULL", (now,result[:80],job_id,row[2]))
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
