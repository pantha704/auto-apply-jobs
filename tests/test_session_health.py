from pathlib import Path

from workflow.session_health import watchdog_session_guidance


def test_watchdog_session_guidance_preserves_distinct_states():
    assert watchdog_session_guidance("valid", None, 144) is None
    assert watchdog_session_guidance("expired", None, 144) == "LINKEDIN SESSION EXPIRED — authentication-required"
    assert watchdog_session_guidance("challenged", None, 144) == "LINKEDIN SESSION CHALLENGED — manual-challenge-resolution-required"
    assert watchdog_session_guidance("unknown", None, 144) == "LINKEDIN SESSION UNKNOWN — probe-inconclusive"


def test_watchdog_session_guidance_is_silent_without_pending_work():
    assert watchdog_session_guidance("expired", None, 0) is None


def test_watchdog_session_guidance_uses_only_approved_safe_detail():
    assert watchdog_session_guidance("expired", "authentication-required", 1) == "LINKEDIN SESSION EXPIRED — authentication-required"
    assert watchdog_session_guidance("expired", "unexpected-detail", 1) == "LINKEDIN SESSION EXPIRED — authentication-required"


def test_watchdog_script_uses_canonical_runtime_and_does_not_hard_fail_session_probe():
    text = (Path(__file__).resolve().parents[1] / "watchdog.py").read_text()
    assert 'os.getenv("JOBHUNT_QUEUE_DB"' in text
    assert "publication_is_ready" in text
    assert 'alert("linkedin_session_unavail"' in text
    assert 'ALERTS.append("linkedin canonical session health unavailable")' not in text
