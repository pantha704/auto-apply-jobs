from __future__ import annotations

import io
import sqlite3
import zipfile

import pytest
from fastapi.testclient import TestClient

from workflow.schema import migrate_control, migrate_queue


def _docx(text: str) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>',
        )
    return out.getvalue()


def _legacy_databases(queue, control):
    with sqlite3.connect(queue) as db:
        db.executescript("""
        CREATE TABLE jobs (id TEXT PRIMARY KEY, portal TEXT, url TEXT, title TEXT, source TEXT,
          status TEXT DEFAULT 'pending', claimed_by TEXT, result TEXT, prio INTEGER DEFAULT 0,
          posted_at TEXT, fetched_at TEXT);
        CREATE TABLE applications (id INTEGER PRIMARY KEY AUTOINCREMENT, portal TEXT NOT NULL,
          company TEXT, role TEXT, url TEXT NOT NULL, applied_at TEXT NOT NULL, status TEXT, url_hash TEXT);
        """)
    with sqlite3.connect(control) as db:
        db.executescript("""
        CREATE TABLE sites (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, base_url TEXT NOT NULL UNIQUE,
          hostname TEXT NOT NULL, adapter TEXT NOT NULL DEFAULT 'auto', auth_type TEXT NOT NULL DEFAULT 'none',
          username_enc BLOB, password_enc BLOB, session_ref TEXT, enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE profile_fields (field TEXT PRIMARY KEY, value_enc BLOB NOT NULL, updated_at TEXT NOT NULL);
        """)


@pytest.fixture()
def workflow_api(tmp_path, monkeypatch):
    queue, control = tmp_path / "queue.db", tmp_path / "control.db"
    _legacy_databases(queue, control)
    migrate_queue(queue)
    migrate_control(control)
    monkeypatch.setenv("JOBHUNT_QUEUE_DB", str(queue))
    monkeypatch.setenv("JOBHUNT_CONTROL_DB", str(control))
    monkeypatch.setenv("JOBHUNT_VAULT_KEY", str(tmp_path / "vault.key"))
    monkeypatch.setenv("JOBHUNT_RESUME_STORAGE", str(tmp_path / "private-resumes"))
    monkeypatch.setenv("JOBHUNT_SESSION_STORAGE", str(tmp_path / "private-sessions"))
    monkeypatch.setenv("JOBHUNT_DASHBOARD_AUTH_DISABLED", "1")
    monkeypatch.setenv("JOBHUNT_PROFILE_BOOTSTRAP", "0")
    monkeypatch.setenv("JOBHUNT_SITE_BOOTSTRAP", "0")
    import controlplane.app as module
    module.settings.cache_clear()
    module.initialize()
    yield TestClient(module.app), queue, control, tmp_path
    module.settings.cache_clear()


def test_analytics_supports_ranges_and_returns_only_metadata(workflow_api):
    client, queue, _, _ = workflow_api
    with sqlite3.connect(queue) as db:
        db.execute("INSERT INTO jobs(id,portal,url,title,status) VALUES('j1','generic','https://secret.test','Secret title','done')")
        db.execute("INSERT INTO application_runs(id,job_id,adapter,lease_token,state,started_at,confirmed) VALUES('r1','j1','generic','secret-lease','submitted','2026-08-19T01:00:00+00:00',1)")
    for range_name in ("24h", "7d", "1m", "3m", "6m", "1y", "all"):
        response = client.get("/api/workflow/analytics", params={"range": range_name})
        assert response.status_code == 200
        assert "Secret title" not in response.text and "secret-lease" not in response.text
    custom = client.get("/api/workflow/analytics", params={"range": "custom", "start": "2026-08-01T00:00:00Z", "end": "2026-08-20T00:00:00Z"})
    assert custom.status_code == 200
    assert custom.json()["confirmed_applications"] == 1
    assert client.get("/api/workflow/analytics", params={"range": "custom"}).status_code == 422


def test_profile_drafts_approval_and_metadata_only_listing(workflow_api):
    client, _, control, _ = workflow_api
    created = client.post("/api/workflow/profiles", json={"facts": {"identity.full_name": "Ada Lovelace", "contact.email": "ada@example.test"}})
    assert created.status_code == 201
    profile_id = created.json()["id"]
    listing = client.get("/api/workflow/profiles").json()
    assert listing["available"] is True
    assert "ada@example.test" not in str(listing)
    detail = client.get(f"/api/workflow/profiles/{profile_id}").json()
    assert detail["facts"]["contact.email"] == "ada@example.test"
    assert client.post(f"/api/workflow/profiles/{profile_id}/approve").status_code == 200
    assert b"ada@example.test" not in control.read_bytes()


def test_private_resume_upload_list_parse_and_review(workflow_api):
    client, _, _, tmp_path = workflow_api
    payload = _docx("Ada Lovelace ada@example.test +1 212 555 0100")
    uploaded = client.post("/api/workflow/resumes", files={"file": ("resume.docx", payload, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert uploaded.status_code == 201
    resume_id = uploaded.json()["id"]
    listing = client.get("/api/workflow/resumes").json()
    assert "storage_key" not in str(listing) and str(tmp_path) not in str(listing)
    parsed = client.post(f"/api/workflow/resumes/{resume_id}/parse")
    assert parsed.status_code == 200
    facts = parsed.json()["facts"]
    assert any(fact["field"] == "email" for fact in facts)
    assert client.put(f"/api/workflow/resume-facts/{facts[0]['id']}", json={"action": "rejected"}).status_code == 200
    bad = client.post("/api/workflow/resumes", files={"file": ("resume.txt", b"private", "text/plain")})
    assert bad.status_code == 422


def test_preferences_answers_tasks_and_trace_are_safe(workflow_api):
    client, queue, control, _ = workflow_api
    preference = client.post("/api/workflow/preferences", json={"version": 1, "rules": [{"criterion": "location", "mode": "hard", "operator": "eq", "expected": "remote"}]})
    assert preference.status_code == 201
    assert client.post("/api/workflow/preferences/1/activate").json()["status"] == "active"

    answer = client.post("/api/workflow/answers", json={"question_key": "work_auth", "answer": "Very private answer", "answer_type": "text", "scope": {"country": "IN"}, "provenance": "operator"})
    assert answer.status_code == 201
    answer_id = answer.json()["id"]
    assert "Very private answer" not in str(client.get("/api/workflow/answers").json())
    assert client.get(f"/api/workflow/answers/{answer_id}").json()["answer"] == "Very private answer"
    assert client.post(f"/api/workflow/answers/{answer_id}/approve").json()["status"] == "approved"
    assert b"Very private answer" not in control.read_bytes()

    with sqlite3.connect(control) as db:
        db.execute("INSERT INTO operator_tasks(id,type,status,safe_summary,created_at) VALUES('t1','manual_review','open','Safe summary','2026-08-19T00:00:00Z')")
    assert client.get("/api/workflow/tasks").json()["items"][0]["safe_summary"] == "Safe summary"
    assert client.post("/api/workflow/tasks/t1/resolve", json={"resolution": "resolved"}).json()["status"] == "resolved"

    with sqlite3.connect(queue) as db:
        db.execute("INSERT INTO jobs(id,portal,url,title,status) VALUES('j1','generic','https://private.test','Private role','done')")
        db.execute("INSERT INTO application_runs(id,job_id,adapter,lease_token,state,started_at,confirmed,safe_detail) VALUES('r1','j1','generic','lease-secret','failed','2026-08-19T01:00:00Z',0,'safe run')")
        db.execute("INSERT INTO job_attempts(run_id,attempt_no,started_at,retryable,safe_detail) VALUES('r1',1,'2026-08-19T01:00:00Z',0,'safe attempt')")
        attempt = db.execute("SELECT id FROM job_attempts").fetchone()[0]
        db.execute("INSERT INTO workflow_actions(run_id,attempt_id,ordinal,action_type,intent,source,status,started_at,safe_detail,input_ref) VALUES('r1',?,1,'fill','email','recipe','done','2026-08-19T01:00:01Z','safe action','candidate.email')", (attempt,))
    runs = client.get("/api/workflow/runs").json()
    assert "Private role" not in str(runs) and "lease-secret" not in str(runs)
    trace = client.get("/api/workflow/runs/r1").json()
    assert trace["attempts"][0]["actions"][0]["input_ref"] == "candidate.email"


def test_session_api_is_live_metadata_only_and_drives_site_readiness(workflow_api):
    client, _, control, _ = workflow_api
    import controlplane.app as module

    with sqlite3.connect(control) as db:
        db.execute(
            """INSERT INTO sites(name,base_url,hostname,adapter,auth_type,session_ref,enabled,created_at,updated_at)
               VALUES('LinkedIn','https://www.linkedin.com/jobs','www.linkedin.com','linkedin','session','legacy-path',1,'now','now')"""
        )
    before = client.get("/api/workflow/sites").json()[0]
    assert before["credential_configured"] is False
    assert before["session_state"] == "unknown"

    service = module._session_manager()
    lease = service.acquire_renewal("linkedin", "test", ttl_seconds=30)
    candidate = service.stage_candidate(
        "linkedin",
        {"cookies": [{"name": "session", "value": "private-value", "domain": ".linkedin.com"}], "origins": []},
        lease.token,
    )
    service.record_probe("linkedin", candidate.id, "valid", lease.token)
    service.promote("linkedin", candidate.id, lease.token)
    service.release_renewal("linkedin", lease.token)

    response = client.get("/api/workflow/sessions")
    assert response.status_code == 200
    text = response.text
    assert "private-value" not in text
    assert "bundle_path" not in text and "digest" not in text and "lease_token" not in text
    session = response.json()["sessions"][0]
    assert session["portal"] == "linkedin" and session["state"] == "valid"
    after = client.get("/api/workflow/sites").json()[0]
    assert after["credential_configured"] is True
    assert after["session_revision"] == 1


def test_unmigrated_databases_report_capabilities_without_mutating(tmp_path, monkeypatch):
    queue, control = tmp_path / "queue.db", tmp_path / "control.db"
    _legacy_databases(queue, control)
    monkeypatch.setenv("JOBHUNT_QUEUE_DB", str(queue))
    monkeypatch.setenv("JOBHUNT_CONTROL_DB", str(control))
    monkeypatch.setenv("JOBHUNT_VAULT_KEY", str(tmp_path / "key"))
    monkeypatch.setenv("JOBHUNT_DASHBOARD_AUTH_DISABLED", "1")
    monkeypatch.setenv("JOBHUNT_PROFILE_BOOTSTRAP", "0")
    monkeypatch.setenv("JOBHUNT_SITE_BOOTSTRAP", "0")
    import controlplane.app as module
    module.settings.cache_clear()
    # Deliberately do not call workflow migrations.
    module.initialize()
    client = TestClient(module.app)
    assert client.get("/api/workflow/profiles").json() == {"available": False, "items": []}
    readiness = client.get("/api/workflow/readiness").json()
    assert readiness["available"] is False and readiness["ready"] is False
    with sqlite3.connect(control) as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='candidate_profiles'").fetchone() is None
    module.settings.cache_clear()
