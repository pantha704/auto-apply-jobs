from title_filter import is_tech_title, title_rejection_reason


def test_internshala_requires_positive_tech_signal():
    rejected = [
        "Copywriting",
        "Lead Management",
        "Interior Design",
        "Civil Engineering",
        "Market Research",
        "Front Desk Intern",
        "Conversational Speech Transcription Expert",
        "Hotel Management (Chef)",
        "Instagram Manager",
    ]
    accepted = [
        "Full Stack Development",
        "Python Development",
        "React Developer",
        "Backend Engineering",
        "Data Science",
        "Machine Learning",
        "UI/UX Design",
        "Software Testing Automation",
    ]
    assert all(not is_tech_title(title, "internshala") for title in rejected)
    assert all(is_tech_title(title, "internshala") for title in accepted)


def test_other_portals_keep_general_filter_semantics():
    assert is_tech_title("Backend Engineer", "wellfound")
    assert not is_tech_title("Content Writer", "wellfound")


def test_real_harvested_senior_and_noise_titles_are_rejected():
    real_rejected = [
        "Senior Software Engineer",
        "Staff Platform Engineer - Americas",
        "Lead developer (Python/ Gen AI)",
        "Engineering Manager: Agentic Integrations",
        "Marketing Operations Architect, AI Automation",
        "STERLING WHITE",
        "Payment Operations Manager",
        "Product Marketing Lead",
    ]
    assert all(title_rejection_reason(title, "wellfound") for title in real_rejected)
    assert title_rejection_reason("Senior Software Engineer", "wellfound") == "seniority-title"
    assert title_rejection_reason("STERLING WHITE", "linkedin") == "non-tech-title"


def test_real_harvested_early_career_tech_titles_stay_eligible():
    real_accepted = [
        "Software Engineer, Applied AI",
        "Backend Engineer",
        "Frontend Engineer",
        "Full Stack Development",
        "Junior Software Engineer",
        "Data Engineer",
    ]
    assert all(is_tech_title(title, "wellfound") for title in real_accepted)
