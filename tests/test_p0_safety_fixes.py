"""Tests for TerminalActionGuard, ReleaseBundle, and other P0 safety fixes."""
from __future__ import annotations

import pytest
from workflow.recovery import ActionCandidate, ActionRisk
from workflow.terminal_guard import (
    TerminalActionGuard,
    TerminalActionRequest,
    TerminalActionResult,
)


class TestTerminalActionGuard:
    """Tests for F08: Terminal action guard."""

    def test_guard_rejects_non_terminal_intent(self):
        guard = TerminalActionGuard()
        request = TerminalActionRequest(
            intent="next",
            candidate_id="c0",
            label="Submit application",
            role="button",
            portal="wellfound",
            session_id="wf-w1",
        )
        candidates = [ActionCandidate("c0", "button", "Submit application")]
        result = guard.validate(request, candidates)
        assert not result.allowed
        assert "not a terminal action" in result.reason

    def test_guard_rejects_non_terminal_label(self):
        guard = TerminalActionGuard()
        request = TerminalActionRequest(
            intent="submit",
            candidate_id="c0",
            label="Continue",
            role="button",
            portal="wellfound",
            session_id="wf-w1",
        )
        candidates = [ActionCandidate("c0", "button", "Continue")]
        result = guard.validate(request, candidates)
        assert not result.allowed
        assert "does not match terminal pattern" in result.reason

    def test_guard_rejects_incompatible_role(self):
        guard = TerminalActionGuard()
        request = TerminalActionRequest(
            intent="submit",
            candidate_id="c0",
            label="Submit application",
            role="textbox",
            portal="wellfound",
            session_id="wf-w1",
        )
        candidates = [ActionCandidate("c0", "button", "Submit application")]
        result = guard.validate(request, candidates)
        assert not result.allowed
        assert "not allowed for terminal actions" in result.reason

    def test_guard_rejects_unknown_candidate(self):
        guard = TerminalActionGuard()
        request = TerminalActionRequest(
            intent="submit",
            candidate_id="c99",
            label="Submit application",
            role="button",
            portal="wellfound",
            session_id="wf-w1",
        )
        candidates = [ActionCandidate("c0", "button", "Submit application")]
        result = guard.validate(request, candidates)
        assert not result.allowed
        assert "not found" in result.reason

    def test_guard_rejects_label_mismatch(self):
        guard = TerminalActionGuard()
        request = TerminalActionRequest(
            intent="submit",
            candidate_id="c0",
            label="Submit application",
            role="button",
            portal="wellfound",
            session_id="wf-w1",
        )
        candidates = [ActionCandidate("c0", "button", "Send application")]
        result = guard.validate(request, candidates)
        assert not result.allowed
        assert "does not match request label" in result.reason

    def test_guard_rejects_role_mismatch(self):
        guard = TerminalActionGuard()
        request = TerminalActionRequest(
            intent="submit",
            candidate_id="c0",
            label="Submit application",
            role="button",
            portal="wellfound",
            session_id="wf-w1",
        )
        candidates = [ActionCandidate("c0", "link", "Submit application")]
        result = guard.validate(request, candidates)
        assert not result.allowed
        assert "does not match request role" in result.reason

    def test_guard_accepts_valid_terminal_action(self):
        guard = TerminalActionGuard()
        request = TerminalActionRequest(
            intent="submit",
            candidate_id="c0",
            label="Submit application",
            role="button",
            portal="wellfound",
            session_id="wf-w1",
        )
        candidates = [ActionCandidate("c0", "button", "Submit application")]
        result = guard.validate(request, candidates)
        assert result.allowed

    def test_guard_accepts_send_intent(self):
        guard = TerminalActionGuard()
        request = TerminalActionRequest(
            intent="send",
            candidate_id="c0",
            label="Send application",
            role="button",
            portal="wellfound",
            session_id="wf-w1",
        )
        candidates = [ActionCandidate("c0", "button", "Send application")]
        result = guard.validate(request, candidates)
        assert result.allowed

    def test_guard_accepts_finalize_intent(self):
        guard = TerminalActionGuard()
        request = TerminalActionRequest(
            intent="finalize",
            candidate_id="c0",
            label="Finalize application",
            role="button",
            portal="wellfound",
            session_id="wf-w1",
        )
        candidates = [ActionCandidate("c0", "button", "Finalize application")]
        result = guard.validate(request, candidates)
        assert result.allowed

    def test_guard_rejects_disallowed_portal(self):
        guard = TerminalActionGuard(allowed_portals=frozenset({"linkedin"}))
        request = TerminalActionRequest(
            intent="submit",
            candidate_id="c0",
            label="Submit application",
            role="button",
            portal="wellfound",
            session_id="wf-w1",
        )
        candidates = [ActionCandidate("c0", "button", "Submit application")]
        result = guard.validate(request, candidates)
        assert not result.allowed
        assert "not in allowed set" in result.reason


class TestReleaseBundle:
    """Tests for F10: Release bundle."""

    def test_release_bundle_is_frozen(self):
        from workflow.release_bundle import ReleaseBundle
        
        bundle = ReleaseBundle(
            portal="wellfound",
            profile_id="p1",
            profile_revision=1,
            resume_id="r1",
            resume_path="/path/to/resume.pdf",
            preference_set_id="ps1",
            preference_set_version=1,
            session_revision=1,
            session_state={"cookies": []},
            facts={"name": "Test"},
            claim_id="c1",
            claim_token="t1",
        )
        
        with pytest.raises(AttributeError):
            bundle.portal = "linkedin"

    def test_release_bundle_fact_lookup(self):
        from workflow.release_bundle import ReleaseBundle
        
        bundle = ReleaseBundle(
            portal="wellfound",
            profile_id="p1",
            profile_revision=1,
            resume_id="r1",
            resume_path="/path/to/resume.pdf",
            preference_set_id="ps1",
            preference_set_version=1,
            session_revision=1,
            session_state={"cookies": []},
            facts={"name": "Test", "email": "test@example.com"},
            claim_id="c1",
            claim_token="t1",
        )
        
        assert bundle.fact("name") == "Test"
        assert bundle.fact("email") == "test@example.com"
        # boolean_fact should raise TypeError for non-boolean values
        with pytest.raises(TypeError, match="must be boolean"):
            bundle.boolean_fact("name")

    def test_release_bundle_boolean_fact(self):
        from workflow.release_bundle import ReleaseBundle
        
        bundle = ReleaseBundle(
            portal="wellfound",
            profile_id="p1",
            profile_revision=1,
            resume_id="r1",
            resume_path="/path/to/resume.pdf",
            preference_set_id="ps1",
            preference_set_version=1,
            session_revision=1,
            session_state={"cookies": []},
            facts={"requires_sponsorship": True},
            claim_id="c1",
            claim_token="t1",
        )
        
        assert bundle.boolean_fact("requires_sponsorship") is True

    def test_release_bundle_missing_fact(self):
        from workflow.release_bundle import ReleaseBundle
        
        bundle = ReleaseBundle(
            portal="wellfound",
            profile_id="p1",
            profile_revision=1,
            resume_id="r1",
            resume_path="/path/to/resume.pdf",
            preference_set_id="ps1",
            preference_set_version=1,
            session_revision=1,
            session_state={"cookies": []},
            facts={},
            claim_id="c1",
            claim_token="t1",
        )
        
        with pytest.raises(KeyError):
            bundle.fact("missing_fact")


class TestUnifiedLocators:
    """Tests for F18: Unified locators."""

    def test_locator_spec_requires_at_least_one_selector(self):
        from workflow.unified_locators import LocatorSpec
        
        with pytest.raises(ValueError, match="locator must have at least one selector"):
            LocatorSpec(role="", name="", text="", css="")

    def test_locator_spec_valid(self):
        from workflow.unified_locators import LocatorSpec
        
        spec = LocatorSpec(role="button", name="Submit")
        assert spec.role == "button"
        assert spec.name == "Submit"

    def test_action_spec_requires_value_for_fill(self):
        from workflow.unified_locators import ActionSpec, LocatorSpec
        
        with pytest.raises(ValueError, match="fill action requires value"):
            ActionSpec(
                action_type="fill",
                intent="fill_name",
                locator=LocatorSpec(role="textbox", name="Name"),
            )

    def test_action_spec_requires_value_for_select(self):
        from workflow.unified_locators import ActionSpec, LocatorSpec
        
        with pytest.raises(ValueError, match="select action requires value"):
            ActionSpec(
                action_type="select",
                intent="select_country",
                locator=LocatorSpec(role="combobox", name="Country"),
            )

    def test_action_spec_valid_click(self):
        from workflow.unified_locators import ActionSpec, LocatorSpec
        
        spec = ActionSpec(
            action_type="click",
            intent="submit",
            locator=LocatorSpec(role="button", name="Submit"),
        )
        assert spec.action_type == "click"
        assert spec.intent == "submit"

    def test_action_spec_valid_fill(self):
        from workflow.unified_locators import ActionSpec, LocatorSpec
        
        spec = ActionSpec(
            action_type="fill",
            intent="fill_name",
            locator=LocatorSpec(role="textbox", name="Name"),
            value="John Doe",
        )
        assert spec.action_type == "fill"
        assert spec.value == "John Doe"

    def test_action_spec_rejects_invalid_type(self):
        from workflow.unified_locators import ActionSpec, LocatorSpec
        
        with pytest.raises(ValueError, match="unsupported action type"):
            ActionSpec(
                action_type="invalid",
                intent="test",
                locator=LocatorSpec(role="button", name="Test"),
            )
