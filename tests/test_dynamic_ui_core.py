"""Pure contract tests for the dynamic UI safety boundary.

These tests intentionally use no network, browser, credentials, profiles, or
answer-bank data. They are the RED tests for the safe intent layer.
"""
import json
from pathlib import Path

import pytest

import dynamic_ui


def test_sanitize_controls_excludes_textbox_values_and_redacts_sensitive_text():
    controls = [
        {"id": "c0", "role": "textbox", "name": "Email", "value": "person@example.com"},
        {"id": "c1", "role": "button", "name": "Apply Now"},
        {"id": "c2", "role": "button", "name": "Call +91 9876543210"},
    ]
    safe = dynamic_ui.sanitize_controls(controls)
    serialized = json.dumps(safe)
    assert "person@example.com" not in serialized
    assert "9876543210" not in serialized
    assert all("value" not in item for item in safe)
    assert any(item["id"] == "c1" for item in safe)


def test_validate_candidate_accepts_only_inventory_id():
    controls = [{"id": "c0", "role": "button", "name": "Apply Now"}]
    assert dynamic_ui.validate_candidate({"candidate_id": "c0"}, controls) == controls[0]
    with pytest.raises(ValueError):
        dynamic_ui.validate_candidate({"candidate_id": "c99"}, controls)
    with pytest.raises(ValueError):
        dynamic_ui.validate_candidate({"css": ".danger"}, controls)


def test_learning_occurs_only_after_verified_postcondition(tmp_path, monkeypatch):
    learned = tmp_path / "selectors.json"
    monkeypatch.setattr(dynamic_ui, "LEARNED", str(learned))
    spec = {"role": "button", "name": "Apply Now", "source": "agent"}
    dynamic_ui.remember_after_verified("wellfound", "apply", spec, verified=False)
    assert not learned.exists()
    dynamic_ui.remember_after_verified("wellfound", "apply", spec, verified=True)
    data = json.loads(learned.read_text())
    assert data["wellfound"]["apply"][0] == spec


def test_llm_payload_contains_only_safe_controls(monkeypatch):
    captured = {}

    def fake_transport(body):
        captured.update(body)
        return {"candidate_id": "c1"}

    controls = [
        {"id": "c0", "role": "textbox", "name": "Email", "value": "person@example.com"},
        {"id": "c1", "role": "button", "name": "Apply Now"},
    ]
    result = dynamic_ui.llm_pick_candidate(controls, "wellfound", "apply", fake_transport)
    assert result["candidate_id"] == "c1"
    body_text = json.dumps(captured)
    assert "person@example.com" not in body_text
    assert "value" not in body_text


def test_submit_candidate_is_rejected_for_untrusted_llm_source():
    controls = [{"id": "c0", "role": "button", "name": "Submit application"}]
    with pytest.raises(ValueError):
        dynamic_ui.validate_action_candidate(
            {"candidate_id": "c0"}, controls, intent="submit", source="llm"
        )


def test_agent_snapshot_script_never_reads_dom_values():
    source = Path(dynamic_ui.__file__).read_text(encoding="utf-8")
    snapshot_block = source.split("def snapshot_a11y", 1)[1].split("def report_miss", 1)[0]
    assert ".value" not in snapshot_block
