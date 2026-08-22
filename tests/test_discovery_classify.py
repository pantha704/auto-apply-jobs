from discovery.classify import classify_row


def _kinds(row):
    return {item["routed"] for item in classify_row(row)}


def test_empty_row_routes_nowhere():
    assert classify_row({}) == []
    assert classify_row({"company": " ", "email": ""}) == []


def test_email_and_job_url_emit_both_routes():
    kinds = _kinds(
        {
            "company": "Acme",
            "email": "jobs@acme.com",
            "apply_url": "https://acme.com/jobs/fullstack-engineer",
        }
    )
    assert kinds == {"cold_email", "apply_candidate"}


def test_greenhouse_board_is_review_not_apply():
    routes = classify_row(
        {
            "company": "Acme",
            "apply_url": "https://boards.greenhouse.io/acme/jobs/123",
        }
    )
    assert {item["routed"] for item in routes} == {"review"}
    assert routes[0]["reason"] == "ats_needs_adapter"


def test_lever_ashby_workday_are_review():
    for url in (
        "https://jobs.lever.co/acme/abc",
        "https://jobs.ashbyhq.com/acme/role",
        "https://acme.myworkdayjobs.com/en-US/careers/job/1",
    ):
        kinds = _kinds({"apply_url": url, "company": "Acme"})
        assert kinds == {"review"}


def test_careers_homepage_is_watchlist_not_job():
    kinds = _kinds({"company": "Acme", "website": "https://acme.com"})
    assert kinds == {"watchlist"}
    kinds = _kinds({"company": "Acme", "careers_url": "https://acme.com/careers"})
    assert kinds == {"watchlist"}


def test_short_or_empty_url_is_not_watchlist():
    kinds = _kinds({"company": "Acme", "careers_url": "https://"})
    assert "watchlist" not in kinds


def test_job_listing_path_is_apply_candidate():
    kinds = _kinds(
        {"company": "Acme", "apply_url": "https://linkedin.com/jobs/view/123"}
    )
    assert kinds == {"apply_candidate"}
