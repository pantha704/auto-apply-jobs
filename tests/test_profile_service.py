from __future__ import annotations

import io
import os
import sqlite3
import zipfile
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from workflow.schema import migrate_control


def _service(tmp_path, **kwargs):
    from workflow.profile_service import ProfileService

    db = tmp_path / "control.db"
    tmp_path.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(db).executescript(
        "CREATE TABLE sites(id INTEGER PRIMARY KEY, name TEXT, base_url TEXT UNIQUE, hostname TEXT, adapter TEXT, auth_type TEXT, enabled INTEGER, created_at TEXT, updated_at TEXT);"
        "CREATE TABLE profile_fields(field TEXT PRIMARY KEY, value_enc BLOB, updated_at TEXT);"
    )
    migrate_control(db)
    return ProfileService(db, tmp_path / "private", Fernet(Fernet.generate_key()), **kwargs)


def _docx(text: str) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    return out.getvalue()


def test_profile_service_does_not_mutate_already_secure_storage_permissions(tmp_path, monkeypatch):
    from workflow.profile_service import ProfileService

    _service(tmp_path)

    def reject_chmod(*args, **kwargs):
        raise AssertionError("secure existing storage must not be chmodded")

    monkeypatch.setattr("workflow.profile_service.os.chmod", reject_chmod)
    ProfileService(tmp_path / "control.db", tmp_path / "private", Fernet(Fernet.generate_key()))


def test_profile_revisions_are_encrypted_immutable_and_only_one_is_approved(tmp_path):
    service = _service(tmp_path)
    first = service.create_profile({"identity.full_name": "Ada Lovelace", "contact.email": "ada@example.test"})
    service.approve_profile(first)
    second = service.revise_profile(first, {"contact.email": "new@example.test"})
    service.approve_profile(second)

    assert service.get_profile(first)["facts"]["contact.email"] == "ada@example.test"
    assert service.get_profile(second)["facts"]["contact.email"] == "new@example.test"
    assert service.get_profile(first)["status"] == "superseded"
    assert service.get_profile(second)["status"] == "approved"
    db = sqlite3.connect(service.db_path)
    raw = b"".join(row[0] for row in db.execute("SELECT value_enc FROM candidate_facts"))
    assert b"Ada Lovelace" not in raw and b"ada@example.test" not in raw


def test_docx_upload_is_private_hashed_parsed_with_offsets_and_no_raw_text_in_db(tmp_path):
    service = _service(tmp_path)
    payload = _docx("Ada Lovelace ada@example.test +1 212 555 0100")
    resume_id = service.upload_resume("resume.docx", payload, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    facts = service.parse_resume(resume_id)

    record = service.get_resume(resume_id)
    stored = Path(record["path"])
    assert stored.stat().st_mode & 0o777 == 0o600
    assert record["sha256"]
    assert any(f["field"] == "email" and f["source_start"] >= 0 and f["source_end"] > f["source_start"] and 0 <= f["confidence"] <= 1 for f in facts)
    db_bytes = Path(service.db_path).read_bytes()
    assert b"ada@example.test" not in db_bytes


def test_upload_rejects_extension_magic_mime_size_and_duplicate(tmp_path):
    service = _service(tmp_path, max_size_bytes=1000)
    with pytest.raises(ValueError, match="PDF or DOCX"):
        service.upload_resume("resume.txt", b"hello", "text/plain")
    with pytest.raises(ValueError, match="signature"):
        service.upload_resume("resume.pdf", b"not pdf", "application/pdf")
    with pytest.raises(ValueError, match="size"):
        service.upload_resume("resume.docx", b"P" * 1001, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    payload = _docx("Ada ada@example.test")
    service = _service(tmp_path / "other")
    service.upload_resume("resume.docx", payload, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    with pytest.raises(ValueError, match="already uploaded"):
        service.upload_resume("copy.docx", payload, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")


def test_parse_fact_actions_create_approved_profile_and_readiness(tmp_path):
    service = _service(tmp_path)
    resume_id = service.upload_resume("resume.docx", _docx("Ada ada@example.test +1 212 555 0100"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    facts = service.parse_resume(resume_id)
    email = next(f for f in facts if f["field"] == "email")
    service.review_parse_fact(email["id"], "edited", "grace@example.test")
    for fact in facts:
        if fact["id"] != email["id"]:
            service.review_parse_fact(fact["id"], "accepted" if fact["field"] == "phone" else "rejected")
    profile_id = service.approve_resume(resume_id)

    assert service.get_profile(profile_id)["facts"]["contact.email"] == "grace@example.test"
    summary = service.readiness_summary()
    assert summary["ready"] is True
    assert summary["approved_profile_id"] == profile_id
    assert summary["approved_resume_id"] == resume_id
