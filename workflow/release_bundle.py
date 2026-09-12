"""Release bundle — one immutable snapshot loaded once per claim.

F10 fix: Workers can no longer fetch session revision and later reload
current session. Instead, they load one immutable release bundle at claim
time that freezes session + profile + resume + policy for the entire run.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ReleaseBundle:
    """Immutable application release bundle loaded once at claim time.
    
    This freezes:
    - Session revision and state
    - Profile ID and revision
    - Resume ID and path
    - Preference set (policy)
    - Portal session revision
    """
    portal: str
    profile_id: str
    profile_revision: int
    resume_id: str
    resume_path: str
    preference_set_id: str
    preference_set_version: int
    session_revision: int
    session_state: Mapping[str, Any]
    facts: Mapping[str, Any]
    claim_id: str
    claim_token: str
    resume_sha256: str = ""

    def fact(self, *aliases: str) -> Any:
        """Get a profile fact by alias chain."""
        for key in aliases:
            value = self.facts.get(key)
            if value is not None and (not isinstance(value, str) or value.strip()):
                return value
        raise KeyError(f"release bundle is missing required fact: {aliases[0]}")

    def boolean_fact(self, *aliases: str) -> bool:
        """Get a boolean profile fact."""
        value = self.fact(*aliases)
        if not isinstance(value, bool):
            raise TypeError(f"release bundle fact must be boolean: {aliases[0]}")
        return value

    def matches_runtime(self, runtime) -> bool:
        """True iff the live approved release still equals the frozen bundle.

        B6: call at dispatch, before browser work. A profile/resume/policy
        change or approval revocation between claim and apply invalidates the
        prepared run — the job goes back for re-planning, never sends stale
        facts. Duck-typed on runtime to avoid an import cycle.
        """
        try:
            return (
                str(runtime.profile_id) == self.profile_id
                and int(runtime.profile_revision) == self.profile_revision
                and str(runtime.resume_id) == self.resume_id
                and str(runtime.preference_set.id) == self.preference_set_id
                and int(runtime.preference_set.version) == self.preference_set_version
                and int(getattr(runtime,"session_revision",0) or 0) == self.session_revision
            )
        except (AttributeError, TypeError, ValueError):
            return False


def create_release_bundle(
    *,
    portal: str,
    profile_id: str,
    profile_revision: int,
    resume_id: str,
    resume_path: str,
    preference_set_id: str,
    preference_set_version: int,
    session_revision: int,
    session_state: Mapping[str, Any],
    facts: Mapping[str, Any],
    claim_id: str,
    claim_token: str,
) -> ReleaseBundle:
    """Create an immutable release bundle from claim-time data."""
    from types import MappingProxyType

    return ReleaseBundle(
        portal=portal,
        profile_id=profile_id,
        profile_revision=profile_revision,
        resume_id=resume_id,
        resume_path=resume_path,
        preference_set_id=preference_set_id,
        preference_set_version=preference_set_version,
        session_revision=session_revision,
        session_state=freeze(session_state),
        facts=freeze(facts),
        claim_id=claim_id,
        claim_token=claim_token,
        resume_sha256=resume_digest(resume_path),
    )


def build_from_runtime(portal: str, runtime, job_id: str, fence_token: str) -> ReleaseBundle:
    """R7: freeze live runtime into immutable bundle at claim time.

    Workers must call this immediately after pin_claim success and carry
    the bundle (not live session/profile lookups) through the run.
    """
    facts = dict(getattr(runtime, "facts", {}) or {})
    session_state = {"revision": int(getattr(runtime, "session_revision", 0) or 0)}
    policy = getattr(runtime, "preference_set", None)
    return create_release_bundle(
        portal=portal,
        profile_id=str(getattr(runtime, "profile_id", "")),
        profile_revision=int(getattr(runtime, "profile_revision", 0) or 0),
        resume_id=str(getattr(runtime, "resume_id", "")),
        resume_path=str(getattr(runtime, "resume_path", "")),
        preference_set_id=str(getattr(policy, "id", "") if policy else ""),
        preference_set_version=int(getattr(policy, "version", 0) if policy else 0),
        session_revision=int(getattr(runtime, "session_revision", 0) or 0),
        session_state=session_state,
        facts=facts,
        claim_id=str(job_id),
        claim_token=str(fence_token or ""),
    )


def resume_digest(path):
    import hashlib
    from pathlib import Path
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).is_file() else ""


def freeze(value):
    from types import MappingProxyType
    if isinstance(value, Mapping):
        return MappingProxyType({k:freeze(v) for k,v in value.items()})
    if isinstance(value,(list,tuple)):
        return tuple(freeze(v) for v in value)
    return value
