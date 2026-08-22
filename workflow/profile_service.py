"""Candidate profile revision and private resume onboarding service."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from cryptography.fernet import Fernet, InvalidToken

from .resume_parser import DOCX_MEDIA_TYPE, PDF_MEDIA_TYPE, parse_resume

_ALLOWED = {".pdf": PDF_MEDIA_TYPE, ".docx": DOCX_MEDIA_TYPE}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProfileService:
    def __init__(self, db_path: str | Path, storage_dir: str | Path, fernet: Fernet | bytes | str, *, max_size_bytes: int = 5 * 1024 * 1024, max_pages: int = 10):
        self.db_path = Path(db_path)
        self.storage_dir = Path(storage_dir)
        if "static" in self.storage_dir.resolve().parts:
            raise ValueError("resume storage must be outside static directories")
        self.storage_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if (self.storage_dir.stat().st_mode & 0o777) != 0o700:
            os.chmod(self.storage_dir, 0o700)
        self.fernet = fernet if isinstance(fernet, Fernet) else Fernet(fernet.encode() if isinstance(fernet, str) else fernet)
        self.max_size_bytes = max_size_bytes
        self.max_pages = max_pages

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _encrypt(self, value: Any) -> bytes:
        return self.fernet.encrypt(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode())

    def _decrypt(self, token: bytes) -> Any:
        try:
            return json.loads(self.fernet.decrypt(token).decode())
        except InvalidToken as exc:
            raise ValueError("candidate data cannot be decrypted with this key") from exc

    @staticmethod
    def _split(key: str) -> tuple[str, str]:
        if "." not in key:
            return "profile", key
        return tuple(key.split(".", 1))  # type: ignore[return-value]

    def create_profile(self, facts: Mapping[str, Any], *, source_resume_version_id: str | None = None, source: str = "user") -> str:
        if source not in {"user", "resume_parser", "legacy_import"}:
            raise ValueError("invalid fact source")
        profile_id = uuid.uuid4().hex
        with self._connect() as db:
            revision = db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM candidate_profiles").fetchone()[0]
            db.execute("INSERT INTO candidate_profiles(id,revision,status,created_at,source_resume_version_id) VALUES(?,?,'draft',?,?)", (profile_id, revision, _now(), source_resume_version_id))
            for key, value in facts.items():
                namespace, field = self._split(key)
                db.execute("INSERT INTO candidate_facts(profile_id,namespace,field,value_enc,value_type,source,user_confirmed) VALUES(?,?,?,?,?,?,?)", (profile_id, namespace, field, self._encrypt(value), type(value).__name__, source, int(source == "user")))
        return profile_id

    def revise_profile(self, profile_id: str, changes: Mapping[str, Any]) -> str:
        old = self.get_profile(profile_id)
        facts = dict(old["facts"])
        facts.update(changes)
        return self.create_profile(facts)

    def approve_profile(self, profile_id: str) -> None:
        with self._connect() as db:
            row = db.execute("SELECT status FROM candidate_profiles WHERE id=?", (profile_id,)).fetchone()
            if row is None:
                raise KeyError(profile_id)
            db.execute("UPDATE candidate_profiles SET status='superseded' WHERE status='approved' AND id<>?", (profile_id,))
            db.execute("UPDATE candidate_profiles SET status='approved',approved_at=? WHERE id=?", (_now(), profile_id))

    def get_profile(self, profile_id: str) -> dict[str, Any]:
        with self._connect() as db:
            profile = db.execute("SELECT * FROM candidate_profiles WHERE id=?", (profile_id,)).fetchone()
            if profile is None:
                raise KeyError(profile_id)
            facts = {f"{row['namespace']}.{row['field']}": self._decrypt(row["value_enc"]) for row in db.execute("SELECT namespace,field,value_enc FROM candidate_facts WHERE profile_id=?", (profile_id,))}
            return {**dict(profile), "facts": facts}

    def upload_resume(self, original_name: str, data: bytes, media_type: str | None = None) -> str:
        suffix = Path(original_name).suffix.lower()
        if suffix not in _ALLOWED:
            raise ValueError("resume must be PDF or DOCX")
        expected = _ALLOWED[suffix]
        if media_type and media_type.split(";", 1)[0].strip().lower() != expected:
            raise ValueError("MIME type does not match extension")
        if not data or len(data) > self.max_size_bytes:
            raise ValueError(f"resume size must be between 1 and {self.max_size_bytes} bytes")
        if suffix == ".pdf" and not data.startswith(b"%PDF-"):
            raise ValueError("invalid PDF signature")
        if suffix == ".docx":
            if not data.startswith(b"PK\x03\x04"):
                raise ValueError("invalid DOCX signature")
            # Parsing here validates the OOXML container and enforces page limits before storage.
        parsed = parse_resume(data, expected, self.max_pages)
        del parsed
        digest = hashlib.sha256(data).hexdigest()
        resume_id = uuid.uuid4().hex
        storage_key = f"{resume_id}{suffix}"
        target = self.storage_dir / storage_key
        with self._connect() as db:
            if db.execute("SELECT 1 FROM resume_versions WHERE sha256=?", (digest,)).fetchone():
                raise ValueError("resume already uploaded")
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                db.execute("INSERT INTO resume_versions(id,sha256,original_name,media_type,size_bytes,storage_key,parse_status,created_at) VALUES(?,?,?,?,?,?,'pending',?)", (resume_id, digest, Path(original_name).name, expected, len(data), storage_key, _now()))
            except Exception:
                target.unlink(missing_ok=True)
                raise
        return resume_id

    def get_resume(self, resume_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM resume_versions WHERE id=?", (resume_id,)).fetchone()
            if row is None:
                raise KeyError(resume_id)
            result = dict(row)
            result["path"] = str(self.storage_dir / row["storage_key"])
            return result

    def parse_resume(self, resume_id: str) -> list[dict[str, Any]]:
        record = self.get_resume(resume_id)
        data = Path(record["path"]).read_bytes()
        parsed = parse_resume(data, record["media_type"], self.max_pages)
        text_digest = hashlib.sha256(parsed.text.encode()).hexdigest()
        with self._connect() as db:
            if record["parse_status"] != "pending":
                raise ValueError("resume has already been parsed or reviewed")
            for fact in parsed.facts:
                db.execute("INSERT INTO resume_parse_facts(resume_version_id,namespace,field,proposed_value_enc,source_page,source_start,source_end,confidence) VALUES(?,?,?,?,?,?,?,?)", (resume_id, fact.namespace, fact.field, self._encrypt(fact.value), fact.source_page, fact.source_start, fact.source_end, fact.confidence))
            db.execute("UPDATE resume_versions SET parser_name='local-deterministic',parser_version='1',parse_status='parsed',extracted_text_sha256=?,parsed_at=? WHERE id=?", (text_digest, _now(), resume_id))
            rows = db.execute("SELECT * FROM resume_parse_facts WHERE resume_version_id=? ORDER BY id", (resume_id,)).fetchall()
            return [self._public_fact(row) for row in rows]

    def _public_fact(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result.pop("proposed_value_enc", None)
        result["value"] = self._decrypt(row["proposed_value_enc"])
        return result

    def list_parse_facts(self, resume_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            return [self._public_fact(row) for row in db.execute("SELECT * FROM resume_parse_facts WHERE resume_version_id=? ORDER BY id", (resume_id,))]

    def review_parse_fact(self, fact_id: int, action: str, edited_value: Any | None = None) -> None:
        if action not in {"accepted", "edited", "rejected"}:
            raise ValueError("action must be accepted, edited, or rejected")
        if action == "edited" and edited_value is None:
            raise ValueError("edited facts require a value")
        with self._connect() as db:
            row = db.execute("SELECT user_action FROM resume_parse_facts WHERE id=?", (fact_id,)).fetchone()
            if row is None:
                raise KeyError(fact_id)
            if row[0] != "pending":
                raise ValueError("parse fact has already been reviewed")
            if action == "edited":
                db.execute("UPDATE resume_parse_facts SET proposed_value_enc=?,user_action=? WHERE id=?", (self._encrypt(edited_value), action, fact_id))
            else:
                db.execute("UPDATE resume_parse_facts SET user_action=? WHERE id=?", (action, fact_id))

    def approve_resume(self, resume_id: str) -> str:
        with self._connect() as db:
            resume = db.execute("SELECT parse_status FROM resume_versions WHERE id=?", (resume_id,)).fetchone()
            rows = db.execute("SELECT * FROM resume_parse_facts WHERE resume_version_id=? ORDER BY id", (resume_id,)).fetchall()
            if resume is None:
                raise KeyError(resume_id)
            if resume[0] != "parsed" or not rows or any(row["user_action"] == "pending" for row in rows):
                raise ValueError("all parsed facts must be reviewed before approval")
        facts = {f"{row['namespace']}.{row['field']}": self._decrypt(row["proposed_value_enc"]) for row in rows if row["user_action"] in {"accepted", "edited"}}
        if not facts:
            raise ValueError("at least one parsed fact must be accepted")
        profile_id = self.create_profile(facts, source_resume_version_id=resume_id, source="resume_parser")
        with self._connect() as db:
            db.execute("UPDATE resume_parse_facts SET approved_profile_id=? WHERE resume_version_id=? AND user_action IN ('accepted','edited')", (profile_id, resume_id))
            db.execute("UPDATE resume_versions SET parse_status='approved',approved_at=? WHERE id=?", (_now(), resume_id))
        self.approve_profile(profile_id)
        return profile_id

    def readiness_summary(self) -> dict[str, Any]:
        with self._connect() as db:
            profile = db.execute("SELECT id FROM candidate_profiles WHERE status='approved' ORDER BY revision DESC LIMIT 1").fetchone()
            resume = db.execute("SELECT id FROM resume_versions WHERE parse_status='approved' ORDER BY approved_at DESC LIMIT 1").fetchone()
        missing = []
        if profile is None: missing.append("approved_profile")
        if resume is None: missing.append("approved_resume")
        return {"ready": not missing, "approved_profile_id": profile[0] if profile else None, "approved_resume_id": resume[0] if resume else None, "missing": missing}
