import jd_match


def test_real_snapshot_sponsorship_wording_is_blocked():
    phrases = [
        "Immigration sponsorship is not available.",
        "Visa sponsorship Not Available",
        "We are unable to provide visa sponsorship for this position.",
        "Candidates must work in the U.S. without current or future sponsorship.",
    ]
    assert all(jd_match.analyze(text)["reason"] == "no-sponsorship" for text in phrases)


def test_real_snapshot_us_location_wording_is_blocked():
    phrases = [
        "Software Engineer — Full Stack - Remote - US Only",
        "This role can be remote within the U.S.",
        "Candidates must be located in the United States.",
    ]
    assert all(jd_match.analyze(text)["reason"] == "us-location-only" for text in phrases)


def test_explicit_three_plus_year_requirements_are_blocked_for_one_yoe_profile():
    blocked = [
        "Requirements: 8+ years of experience in software engineering.",
        "Minimum 3 years of relevant experience with React and Node.js.",
        "3-5 years of professional experience building Python services.",
    ]
    assert all(jd_match.analyze(text)["reason"] == "experience-required" for text in blocked)
    assert jd_match.minimum_required_experience("2-3 years of experience") == 2
    assert jd_match.analyze("2-3 years of experience with React and Node.js")["decision"] == "apply"


def test_authorization_question_alone_does_not_fabricate_a_blocker():
    text = "Are you legally authorized to work in the United States? Yes or No"
    assert jd_match.analyze(text)["decision"] == "apply"