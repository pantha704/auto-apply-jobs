from pathlib import Path

from submission_signals import has_submission_confirmation


def test_specific_confirmation_text_is_accepted():
    accepted = [
        "Your application was sent",
        "Application submitted",
        "Thanks for applying",
        "We've received your application",
        "Applied successfully",
    ]
    assert all(has_submission_confirmation(text) for text in accepted)


def test_ambiguous_ui_changes_are_not_submission_proof():
    rejected = [
        "Success tips for your job search",
        "Dialog closed",
        "Send",
        "Application form",
        "Your profile was successfully updated",
    ]
    assert all(not has_submission_confirmation(text) for text in rejected)


def test_http_statuses_are_ignored_body_text_is_required():
    """F05: HTTP status codes alone are NOT confirmation. Only body text qualifies."""
    assert not has_submission_confirmation(http_statuses=[201])
    assert has_submission_confirmation("Application submitted")
    assert not has_submission_confirmation(http_statuses=[302, 400, 500])


def test_internshala_records_after_submit_screenshot_on_confirmed_apply():
    text = (Path(__file__).resolve().parents[1] / "worker_internshala.py").read_text()
    assert "record_submission_proof(" in text
    assert 'status_if_confirmed="applied"' in text
