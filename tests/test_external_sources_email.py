import sqlite3

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def dashboard(tmp_path, monkeypatch):
    queue = tmp_path / "apply_queue.db"
    control = tmp_path / "controlplane.db"
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-test")
    with sqlite3.connect(queue) as db:
        db.execute(
            "CREATE TABLE jobs (id TEXT PRIMARY KEY, portal TEXT, url TEXT, title TEXT, source TEXT, status TEXT, claimed_by TEXT, result TEXT, prio INTEGER DEFAULT 0, fetched_at TEXT)"
        )
        db.execute(
            "CREATE TABLE applications (id INTEGER PRIMARY KEY, portal TEXT, company TEXT, role TEXT, url TEXT, applied_at TEXT, answers TEXT, resume_used TEXT, status TEXT, note TEXT, snap_before TEXT, snap_after TEXT, url_hash TEXT)"
        )
        db.execute(
            "INSERT INTO applications VALUES (1,'linkedin','Acme','Developer','https://example.test/b','2026-08-19T01:00:00+00:00',NULL,NULL,'submitted','',NULL,NULL,'hash')"
        )
    monkeypatch.setenv("JOBHUNT_QUEUE_DB", str(queue))
    monkeypatch.setenv("JOBHUNT_CONTROL_DB", str(control))
    monkeypatch.setenv("JOBHUNT_VAULT_KEY", str(tmp_path / "vault.key"))
    monkeypatch.setenv("JOBHUNT_RESUME", str(resume))
    monkeypatch.setenv("JOBHUNT_DASHBOARD_AUTH_DISABLED", "1")
    monkeypatch.setenv("JOBHUNT_PROFILE_BOOTSTRAP", "0")
    monkeypatch.setenv("JOBHUNT_SITE_BOOTSTRAP", "0")
    import controlplane.app as app_module

    app_module.settings.cache_clear()
    app_module.initialize()
    yield TestClient(app_module.app), control, queue
    app_module.settings.cache_clear()


def test_external_source_and_manual_contact_flow_is_not_an_application(dashboard):
    client, _, queue = dashboard
    source = client.post(
        "/api/external-sources",
        json={"name": "Manual companies", "url": "manual://companies", "kind": "manual"},
    )
    assert source.status_code == 201
    source_id = source.json()["id"]

    contact = client.post(
        "/api/cold-email/contacts",
        json={
            "company": "Acme",
            "email": "careers@acme.test",
            "role": "Full Stack Developer",
            "website": "https://acme.test",
            "source_id": source_id,
        },
    )
    assert contact.status_code == 201
    listing = client.get("/api/cold-email/contacts").json()
    assert listing["counts"]["queued"] == 1
    assert listing["items"][0]["email"] == "careers@acme.test"

    confirmed = sqlite3.connect(queue).execute(
        "SELECT COUNT(*) FROM applications WHERE status IN ('submitted','applied')"
    ).fetchone()[0]
    assert confirmed == 1


def test_template_renders_editable_exact_draft_without_manual_send_url(dashboard):
    client, _, _ = dashboard
    source_id = client.post(
        "/api/external-sources",
        json={"name": "Manual", "url": "manual://cold", "kind": "manual"},
    ).json()["id"]
    contact_id = client.post(
        "/api/cold-email/contacts",
        json={"company": "Acme", "email": "jobs@acme.test", "role": "Engineer", "source_id": source_id},
    ).json()["id"]
    template = client.post(
        "/api/cold-email/templates",
        json={
            "name": "Direct introduction",
            "subject": "Application for {{role}} at {{company}}",
            "body": "Hello {{company}} team,\n\nI am interested in the {{role}} role.\n\nRegards,\nPratham",
            "is_default": True,
        },
    )
    assert template.status_code == 201
    template_id = template.json()["id"]

    draft = client.post(
        f"/api/cold-email/contacts/{contact_id}/draft",
        json={"template_id": template_id},
    )
    assert draft.status_code == 200
    body = draft.json()
    assert body["subject"] == "Application for Engineer at Acme"
    assert "Acme team" in body["body"]
    assert "gmail_compose_url" not in body
    assert client.get("/api/cold-email/contacts").json()["counts"]["drafted"] == 1


def test_operator_ui_exposes_sources_and_manual_gmail_lane(dashboard):
    client, _, _ = dashboard
    page = client.get("/")
    assert page.status_code == 200
    assert 'data-page="sources"' in page.text
    assert 'data-page="cold-email"' in page.text
    assert 'id="source-form"' in page.text
    assert 'id="cold-contact-form"' in page.text
    assert "APPROVAL REQUIRED" in page.text
    assert "explicitly approve" in page.text
    script = client.get("/static/app.js").text
    assert "/api/external-sources" in script
    assert "/api/cold-email/contacts" in script
    assert "gmail_compose_url" not in script
    assert "/mark-sent" not in script
    assert "I sent this" not in script
    assert "I already sent this" not in script


def test_manual_mark_sent_is_disabled_and_cannot_bypass_provider_confirmation(dashboard):
    client, control, queue = dashboard
    source_id = client.post(
        "/api/external-sources",
        json={"name": "Manual", "url": "manual://send", "kind": "manual"},
    ).json()["id"]
    contact_id = client.post(
        "/api/cold-email/contacts",
        json={"company": "Acme", "email": "jobs@acme.test", "source_id": source_id},
    ).json()["id"]
    client.post(f"/api/cold-email/contacts/{contact_id}/draft", json={})
    approved = client.post(
        f"/api/cold-email/contacts/{contact_id}/approve-send",
        json={"confirmed": True},
    )
    assert approved.status_code == 200

    marked = client.post(
        f"/api/cold-email/contacts/{contact_id}/mark-sent",
        json={"confirmed": True, "provider_id": "gmail-manual"},
    )
    assert marked.status_code == 410
    assert client.post(f"/api/cold-email/contacts/{contact_id}/send", json={}).status_code == 404
    with sqlite3.connect(control) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM cold_email_sends WHERE contact_id=? AND status='queued'",
            (contact_id,),
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM cold_email_sends WHERE contact_id=? AND status='sent'",
            (contact_id,),
        ).fetchone()[0] == 0

    confirmed = sqlite3.connect(queue).execute(
        "SELECT COUNT(*) FROM applications WHERE status IN ('submitted','applied')"
    ).fetchone()[0]
    assert confirmed == 1


def test_approved_draft_is_queued_for_sender_and_visible_in_progress(dashboard):
    client, _, _ = dashboard
    contact_id = client.post(
        "/api/cold-email/contacts",
        json={"company": "Acme", "email": "approved@acme.test", "role": "Engineer"},
    ).json()["id"]
    client.post(f"/api/cold-email/contacts/{contact_id}/draft", json={})
    assert client.post(
        f"/api/cold-email/contacts/{contact_id}/approve-send", json={"confirmed": False}
    ).status_code == 409
    approved = client.post(
        f"/api/cold-email/contacts/{contact_id}/approve-send", json={"confirmed": True}
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "queued"
    progress = client.get("/api/cold-email/progress").json()
    assert progress["counts"]["queued"] == 1
    assert progress["provider"]["kind"] == "gmail_api"
    assert progress["source_of_truth"] == "sqlite"
    assert progress["event_projection"] == "jsonl"


def test_operator_ui_exposes_sender_queue_and_history(dashboard):
    client, _, _ = dashboard
    page = client.get("/").text
    script = client.get("/static/app.js").text
    assert 'id="cold-sender-status"' in page
    assert 'id="cold-send-history"' in page
    assert "/api/cold-email/progress" in script
    assert "approve-send" in script
