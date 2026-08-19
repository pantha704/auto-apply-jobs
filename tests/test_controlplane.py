import sqlite3

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def dashboard(tmp_path, monkeypatch):
    queue = tmp_path / "apply_queue.db"
    control = tmp_path / "controlplane.db"
    key = tmp_path / "vault.key"
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-test")

    conn = sqlite3.connect(queue)
    conn.execute(
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, portal TEXT, url TEXT, title TEXT, source TEXT, status TEXT, claimed_by TEXT, result TEXT, prio INTEGER DEFAULT 0, fetched_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE applications (id INTEGER PRIMARY KEY, portal TEXT, company TEXT, role TEXT, url TEXT, applied_at TEXT, answers TEXT, resume_used TEXT, status TEXT, note TEXT, snap_before TEXT, snap_after TEXT, url_hash TEXT)"
    )
    conn.executemany(
        "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (
                "a",
                "wellfound",
                "https://example.test/a",
                "Engineer",
                "test",
                "pending",
                None,
                None,
                5,
                "2026-08-19T00:00:00+00:00",
            ),
            (
                "b",
                "linkedin",
                "https://example.test/b",
                "Developer",
                "test",
                "done",
                "li-w1",
                "submitted",
                5,
                "2026-08-19T00:00:00+00:00",
            ),
        ],
    )
    conn.execute(
        "INSERT INTO applications VALUES (1,'linkedin','Acme','Developer','https://example.test/b','2026-08-19T01:00:00+00:00',NULL,NULL,'submitted','',NULL,NULL,'hash')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("JOBHUNT_QUEUE_DB", str(queue))
    monkeypatch.setenv("JOBHUNT_CONTROL_DB", str(control))
    monkeypatch.setenv("JOBHUNT_VAULT_KEY", str(key))
    monkeypatch.setenv("JOBHUNT_RESUME", str(resume))
    monkeypatch.setenv("JOBHUNT_DASHBOARD_AUTH_DISABLED", "1")
    monkeypatch.setenv("JOBHUNT_PROFILE_BOOTSTRAP", "0")
    monkeypatch.setenv("JOBHUNT_SITE_BOOTSTRAP", "0")

    import controlplane.app as app_module

    app_module.settings.cache_clear()
    app_module.initialize()
    return TestClient(app_module.app), control, key


def test_overview_reports_queue_and_confirmed_submissions(dashboard):
    client, _, _ = dashboard
    response = client.get("/api/overview")
    assert response.status_code == 200
    body = response.json()
    assert body["queue"]["pending"] == 1
    assert body["queue"]["done"] == 1
    assert body["applications"]["confirmed"] == 1
    assert body["health"] in {"healthy", "attention"}


def test_site_credentials_are_encrypted_and_never_returned(dashboard):
    client, control, key = dashboard
    response = client.post(
        "/api/sites",
        json={
            "name": "Example Careers",
            "base_url": "https://careers.example.com/jobs",
            "auth_type": "password",
            "username": "person@example.com",
            "password": "correct-horse-battery-staple",
            "adapter": "auto",
            "enabled": True,
        },
    )
    assert response.status_code == 201
    site = response.json()
    assert site["credential_configured"] is True
    assert "password" not in site

    raw = control.read_bytes()
    assert b"correct-horse-battery-staple" not in raw
    assert key.exists()
    listed = client.get("/api/sites").json()
    assert listed[0]["username_masked"].startswith("p")
    assert "person@example.com" not in str(listed)


def test_readiness_identifies_missing_profile_and_unsupported_adapter(dashboard):
    client, _, _ = dashboard
    client.post(
        "/api/sites",
        json={
            "name": "Unknown Board",
            "base_url": "https://jobs.unknown.example",
            "auth_type": "none",
            "adapter": "auto",
            "enabled": True,
        },
    )
    readiness = client.get("/api/readiness").json()
    codes = {item["code"] for item in readiness["issues"]}
    assert "profile_incomplete" in codes
    assert "adapter_unresolved" in codes
    assert readiness["ready"] is False


def test_profile_update_makes_required_fields_complete_without_exposing_values(
    dashboard,
):
    client, control, _ = dashboard
    response = client.put(
        "/api/profile",
        json={
            "full_name": "Test Person",
            "email": "person@example.com",
            "phone": "+91 9876543210",
            "city": "Kolkata",
            "country": "India",
            "years_experience": 1,
            "work_authorization": "India",
            "sponsorship_required": True,
        },
    )
    assert response.status_code == 200
    body = client.get("/api/profile/status").json()
    assert body["complete"] is True
    assert "person@example.com" not in str(body)
    assert b"person@example.com" not in control.read_bytes()


def test_invalid_site_url_is_rejected(dashboard):
    client, _, _ = dashboard
    response = client.post(
        "/api/sites",
        json={"name": "Bad", "base_url": "javascript:alert(1)", "auth_type": "none"},
    )
    assert response.status_code == 422


def test_application_history_is_paginated_and_redacted(dashboard):
    client, _, _ = dashboard
    response = client.get("/api/applications?limit=10&offset=0")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == "submitted"
    assert "answers" not in body["items"][0]
    assert {"url", "note", "resume_used"}.isdisjoint(body["items"][0])


def test_readiness_blocks_when_no_site_is_enabled(dashboard):
    client, _, _ = dashboard
    body = client.get("/api/readiness").json()
    assert body["ready"] is False
    assert "no_enabled_sites" in {item["code"] for item in body["issues"]}


@pytest.mark.parametrize(
    "payload",
    [
        {"auth_type": "password", "username": "person@example.com"},
        {"auth_type": "password", "password": "secret"},
        {"auth_type": "session", "password": "secret"},
    ],
)
def test_site_auth_material_must_match_auth_type(dashboard, payload):
    client, _, _ = dashboard
    response = client.post(
        "/api/sites",
        json={"name": "Example", "base_url": "https://example.com/jobs", **payload},
    )
    assert response.status_code == 422


def test_worker_action_rejects_unrecognized_unit_or_action(dashboard):
    client, _, _ = dashboard
    assert client.post("/api/workers/not-a-worker/restart").status_code == 404
    assert client.post("/api/workers/jobhunt-li@w1.service/destroy").status_code == 404


def test_authentication_covers_api_root_and_static_assets(dashboard, monkeypatch):
    from importlib import import_module

    app_module = import_module("controlplane.app")
    monkeypatch.setenv("JOBHUNT_DASHBOARD_AUTH_DISABLED", "0")
    monkeypatch.setenv("JOBHUNT_DASHBOARD_USER", "operator")
    monkeypatch.setenv("JOBHUNT_DASHBOARD_PASSWORD", "test-secret")
    app_module.settings.cache_clear()
    try:
        with TestClient(app_module.app) as client:
            assert client.get("/livez").status_code == 200
            assert client.get("/readyz").status_code == 200
            for path in ("/", "/api/health", "/static/app.js", "/static/app.css"):
                assert client.get(path).status_code == 401
                assert client.get(path, auth=("operator", "wrong")).status_code == 401
                assert (
                    client.get(path, auth=("operator", "test-secret")).status_code
                    == 200
                )
            assert (
                client.get(
                    "/api/health", headers={"Authorization": "Basic !!!"}
                ).status_code
                == 401
            )
            auth = ("operator", "test-secret")
            mutation = "/api/workers/not-a-worker/restart"
            assert client.post(mutation, auth=auth).status_code == 403
            assert (
                client.post(
                    mutation, auth=auth, headers={"X-Jobhunt-CSRF": "1"}
                ).status_code
                == 404
            )
            assert (
                client.post(
                    mutation,
                    auth=auth,
                    headers={
                        "X-Jobhunt-CSRF": "1",
                        "Origin": "https://evil.example",
                    },
                ).status_code
                == 403
            )
            protected = client.get("/api/health", auth=auth)
            assert protected.headers["x-frame-options"] == "DENY"
            assert protected.headers["referrer-policy"] == "no-referrer"
    finally:
        app_module.settings.cache_clear()
