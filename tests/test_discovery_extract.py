from discovery.extract import map_headers


def test_company_column_wins_over_generic_name():
    mapping = map_headers(["Name", "Company Name", "Email", "Careers"])
    assert mapping["company"] == "Company Name"
    assert mapping["email"] == "Email"
    assert mapping["careers_url"] == "Careers"


def test_name_is_company_only_when_no_company_column():
    mapping = map_headers(["Name", "Website"])
    assert mapping["company"] == "Name"
    assert mapping["website"] == "Website"


def test_unknown_headers_are_ignored():
    mapping = map_headers(["foo", "bar"])
    assert mapping == {}
