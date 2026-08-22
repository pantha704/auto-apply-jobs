"""Privacy-safe operational guidance for canonical portal sessions."""
from __future__ import annotations

_DEFAULT_DETAIL = {
    "expired": "authentication-required",
    "challenged": "manual-challenge-resolution-required",
    "unknown": "probe-inconclusive",
    "missing": "renewal-required",
}
_ALLOWED_DETAIL = {
    "authenticated-endpoint-accepted",
    "authentication-required",
    "manual-challenge-resolution-required",
    "challenge-detected",
    "probe-inconclusive",
    "probe-network-error",
    "renewal-required",
}


def watchdog_session_guidance(
    state: str,
    safe_detail: str | None,
    pending: int,
    *,
    portal_label: str = "LINKEDIN",
) -> str | None:
    """Return a metadata-only alert while keeping health states distinct."""
    if pending <= 0 or state == "valid":
        return None
    normalized = state if state in _DEFAULT_DETAIL else "unknown"
    detail = safe_detail if safe_detail in _ALLOWED_DETAIL else _DEFAULT_DETAIL[normalized]
    return f"{portal_label} SESSION {normalized.upper()} — {detail}"
