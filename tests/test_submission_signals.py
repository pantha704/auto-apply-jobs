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


def test_only_scoped_success_http_statuses_are_accepted():
    assert has_submission_confirmation(http_statuses=[201])
    assert not has_submission_confirmation(http_statuses=[302, 400, 500])
