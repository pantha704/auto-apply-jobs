import hashlib
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
    return TestClient(app_module.app), control, queue


def _enable_ingest(control, token="trial-token"):
    digest = hashlib.sha256(token.encode()).hexdigest()
    db = sqlite3.connect(control)
    db.execute("UPDATE control_flags SET value='1' WHERE key='ingest_enabled'")
    db.execute(
        "INSERT OR REPLACE INTO control_flags(key, value) VALUES('ingest_token_sha256', ?)",
        (digest,),
    )
    db.execute(
        """INSERT INTO external_sources(id,name,url,kind,status,owner,created_at,updated_at)
           VALUES('src-1','India','https://example.test/sheet','sheet','queued','n8n','t','t')"""
    )
    db.commit()
    db.close()


def test_ingest_disabled_returns_409(dashboard):
    client, _, _ = dashboard
    response = client.post(
        "/api/ingest/batch",
        json={"source_id": "src-1", "entities": []},
        headers={"X-Jobhunt-Ingest": "trial-token"},
    )
    assert response.status_code == 409


def test_ingest_batch_does_not_change_confirmed(dashboard):
    client, control, queue = dashboard
    _enable_ingest(control)
    before = sqlite3.connect(queue).execute(
        "SELECT COUNT(*) FROM applications WHERE status IN ('submitted','applied')"
    ).fetchone()[0]
    response = client.post(
        "/api/ingest/batch",
        json={
            "source_id": "src-1",
            "entities": [
                {
                    "company": "Acme",
                    "email": "jobs@acme.com",
                    "apply_url": "https://acme.com/jobs/eng",
                    "website": "https://acme.com",
                },
                {
                    "company": "GH Co",
                    "apply_url": "https://boards.greenhouse.io/ghco/jobs/1",
                },
            ],
        },
        headers={"X-Jobhunt-Ingest": "trial-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["confirmed_before"] == before
    assert body["confirmed_after"] == before
    assert body["routed_email"] >= 1
    assert body["routed_review"] >= 1
    after = sqlite3.connect(queue).execute(
        "SELECT COUNT(*) FROM applications WHERE status IN ('submitted','applied')"
    ).fetchone()[0]
    assert after == before
    jobs = sqlite3.connect(queue).execute("SELECT url FROM jobs").fetchall()
    assert not any("greenhouse" in (row[0] or "") for row in jobs)


def test_supported_external_source_enters_worker_queue_unknown_enters_review(dashboard):
    client, control, queue = dashboard
    _enable_ingest(control)
    response = client.post(
        "/api/ingest/batch",
        json={
            "source_id": "src-1",
            "entities": [
                {
                    "company": "Remote Co",
                    "role": "Full Stack Engineer",
                    "apply_url": "https://weworkremotely.com/remote-jobs/remote-co-full-stack-engineer",
                },
                {
                    "company": "Unknown Co",
                    "role": "Software Engineer",
                    "apply_url": "https://unknown.example/jobs/123",
                },
            ],
        },
        headers={"X-Jobhunt-Ingest": "trial-token"},
    )
    assert response.status_code == 200
    assert response.json()["routed_apply"] == 1
    jobs = sqlite3.connect(queue).execute(
        "SELECT portal,source,status,url FROM jobs ORDER BY source"
    ).fetchall()
    assert jobs == [
        (
            "external",
            "weworkremotely",
            "pending",
            "https://weworkremotely.com/remote-jobs/remote-co-full-stack-engineer",
        )
    ]
    reviews = sqlite3.connect(control).execute(
        "SELECT type,status,safe_summary FROM operator_tasks"
    ).fetchall()
    assert reviews == [("manual_review", "open", "External apply URL needs an adapter")]


def test_ingest_rejects_more_than_200_entities(dashboard):
    client, control, _ = dashboard
    _enable_ingest(control)
    response = client.post(
        "/api/ingest/batch",
        json={"source_id": "src-1", "entities": [{"company": str(i)} for i in range(201)]},
        headers={"X-Jobhunt-Ingest": "trial-token"},
    )
    assert response.status_code == 400
