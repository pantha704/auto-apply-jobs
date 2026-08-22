from __future__ import annotations

import io
import sqlite3
import zipfile

import pytest
from cryptography.fernet import Fernet

from workflow.application_gate import (
    PublicationUnavailable,
    pin_claim,
    published_runtime,
)
from workflow.portal_session_runtime import clear_cache, session_manager
from workflow.profile_service import ProfileService
from workflow.preferences import PreferenceRepository
from workflow.schema import migrate_control, migrate_queue


def configured_publication(tmp_path, monkeypatch):
    control = tmp_path / "control.db"
    queue = tmp_path / "queue.db"
    key_path = tmp_path / "vault.key"
    resumes = tmp_path / "resumes"
    sessions = tmp_path / "sessions"
    key = Fernet.generate_key()
    key_path.write_bytes(key)
    migrate_control(control)
    with sqlite3.connect(queue) as db:
        db.executescript(
            """CREATE TABLE jobs (
              id TEXT PRIMARY KEY, portal TEXT, title TEXT, company TEXT,
              location TEXT, url TEXT NOT NULL, prio INTEGER DEFAULT 0,
              status TEXT DEFAULT 'pending', result TEXT, added_at TEXT,
              updated_at TEXT, claimed_by TEXT
            );
            CREATE TABLE applications (
              id INTEGER PRIMARY KEY, portal TEXT, company TEXT, role TEXT,
              status TEXT, applied_at TEXT, url TEXT
            );"""
        )
    migrate_queue(queue)
    monkeypatch.setenv("JOBHUNT_CONTROL_DB", str(control))
    monkeypatch.setenv("JOBHUNT_QUEUE_DB", str(queue))
    monkeypatch.setenv("JOBHUNT_VAULT_KEY", str(key_path))
    monkeypatch.setenv("JOBHUNT_RESUME_STORAGE", str(resumes))
    monkeypatch.setenv("JOBHUNT_SESSION_STORAGE", str(sessions))
    clear_cache()

    profiles = ProfileService(control, resumes, key)
    profile_id = profiles.create_profile(
        {
            "profile.note": "Approved note",
            "profile.stack": "Python, TypeScript",
            "profile.years": "1",
            "profile.requires_sponsorship": True,
        }
    )
    profiles.approve_profile(profile_id)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Private fixture</w:t></w:r></w:p></w:body></w:document>')
    resume_id = profiles.upload_resume(
        "resume.docx", payload.getvalue(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    with sqlite3.connect(control) as db:
        db.execute(
            "UPDATE resume_versions SET parse_status='approved',approved_at='2026-08-22T00:00:00+00:00' WHERE id=?",
            (resume_id,),
        )

    prefs = PreferenceRepository(control)
    prefs.create_set(
        version=1,
        set_id="policy-1",
        rules=[
            {
                "criterion": "title",
                "mode": "hard",
                "operator": "contains",
                "expected": "engineer",
                "unknown_policy": "block",
            }
        ],
    )
    prefs.activate(1)

    manager = session_manager()
    lease = manager.acquire_renewal("yc", "test-owner")
    candidate = manager.stage_candidate(
        "yc",
        {"cookies": [{"name": "session", "value": "private", "domain": ".example.test", "path": "/"}], "origins": []},
        lease.token,
    )
    manager.record_probe("yc", candidate.id, "valid", lease.token, "authenticated-endpoint")
    manager.promote("yc", candidate.id, lease.token)
    manager.release_renewal("yc", lease.token)
    return control, queue, profile_id, resume_id


def test_publication_gate_fails_closed_without_approved_release(tmp_path, monkeypatch):
    control = tmp_path / "control.db"
    key_path = tmp_path / "key"
    key_path.write_bytes(Fernet.generate_key())
    migrate_control(control)
    monkeypatch.setenv("JOBHUNT_CONTROL_DB", str(control))
    monkeypatch.setenv("JOBHUNT_VAULT_KEY", str(key_path))
    monkeypatch.setenv("JOBHUNT_RESUME_STORAGE", str(tmp_path / "resumes"))
    monkeypatch.setenv("JOBHUNT_SESSION_STORAGE", str(tmp_path / "sessions"))
    clear_cache()
    with pytest.raises(PublicationUnavailable):
        published_runtime("yc")


def test_publication_gate_loads_and_atomically_pins_all_revisions(tmp_path, monkeypatch):
    _, queue, profile_id, resume_id = configured_publication(tmp_path, monkeypatch)
    runtime = published_runtime(
        "yc",
        (("profile.note",), ("profile.stack",), ("profile.years",), ("profile.requires_sponsorship",)),
    )
    assert runtime.profile_id == profile_id
    assert runtime.resume_id == resume_id
    assert runtime.boolean_fact("profile.requires_sponsorship") is True
    assert runtime.evaluate({"title": "Software Engineer"}).eligible is True

    with sqlite3.connect(queue) as db:
        db.execute(
            "INSERT INTO jobs(id,portal,url,title,status) VALUES('job-1','yc','https://example.test','Software Engineer','pending')"
        )
        assert pin_claim(db, "job-1", "worker-1", runtime) == 1
        row = db.execute(
            """SELECT status,candidate_profile_id,candidate_profile_revision,
               resume_version_id,preference_set_id,preference_set_version,
               portal_session_revision FROM jobs WHERE id='job-1'"""
        ).fetchone()
    assert row == (
        "claimed",
        profile_id,
        runtime.profile_revision,
        resume_id,
        "policy-1",
        1,
        runtime.session_revision,
    )
    clear_cache()
