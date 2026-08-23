from __future__ import annotations

import os
import sqlite3
from pathlib import Path


class FakePage:
    def __init__(self, body: str, png: bytes | None = b"\x89PNG\r\n", html: str = "<html></html>", fail_shot: bool = False):
        self.body = body
        self.png = png
        self.html = html
        self.fail_shot = fail_shot

    def inner_text(self, _selector: str) -> str:
        return self.body

    def content(self) -> str:
        return self.html

    def screenshot(self, path: str, full_page: bool = False) -> None:
        if self.fail_shot or self.png is None:
            raise RuntimeError("screenshot failed")
        Path(path).write_bytes(self.png)


def _row(db_path: Path):
    return sqlite3.connect(db_path).execute(
        "SELECT status, snap_before, snap_after FROM applications"
    ).fetchone()


def test_record_submission_proof_requires_after_png_for_confirmed(tmp_path, monkeypatch):
    import audit

    monkeypatch.setattr(audit, "DB", str(tmp_path / "q.db"))
    monkeypatch.setattr(audit, "AUDIT", str(tmp_path / "audits"))
    page = FakePage(body="Your application was sent")
    result = audit.record_submission_proof(
        page,
        portal="internshala",
        job_id="job-1",
        company="",
        role="Backend Intern",
        url="https://example.test/a",
        status_if_confirmed="applied",
        resume_used="/tmp/r.pdf",
    )
    assert result.confirmed is True
    assert result.status == "applied"
    assert result.snap_after
    assert Path(result.snap_after).is_file()
    assert Path(result.snap_after).stat().st_size > 0
    assert os.stat(result.snap_after).st_mode & 0o777 == 0o600
    row = _row(tmp_path / "q.db")
    assert row[0] == "applied"
    assert row[1] == result.snap_before
    assert row[2] == result.snap_after


def test_missing_after_png_is_unconfirmed_even_if_text_matches(tmp_path, monkeypatch):
    import audit

    monkeypatch.setattr(audit, "DB", str(tmp_path / "q.db"))
    monkeypatch.setattr(audit, "AUDIT", str(tmp_path / "audits"))
    page = FakePage(body="Your application was sent", fail_shot=True)
    result = audit.record_submission_proof(
        page,
        portal="internshala",
        job_id="job-2",
        company="",
        role="Backend Intern",
        url="https://example.test/b",
        status_if_confirmed="applied",
    )
    assert result.confirmed is False
    assert result.status == "submit-unconfirmed"
    row = _row(tmp_path / "q.db")
    assert row[0] == "submit-unconfirmed"
    assert row[2] is None


def test_unconfirmed_row_can_be_upgraded_when_retry_has_proof(tmp_path, monkeypatch):
    import audit

    monkeypatch.setattr(audit, "DB", str(tmp_path / "q.db"))
    monkeypatch.setattr(audit, "AUDIT", str(tmp_path / "audits"))
    url = "https://example.test/c"
    first = audit.record_submission_proof(
        FakePage(body="Your application was sent", fail_shot=True),
        portal="internshala",
        job_id="job-3",
        company="",
        role="Backend Intern",
        url=url,
        status_if_confirmed="applied",
    )
    assert first.confirmed is False
    second = audit.record_submission_proof(
        FakePage(body="Your application was sent"),
        portal="internshala",
        job_id="job-3",
        company="",
        role="Backend Intern",
        url=url,
        status_if_confirmed="applied",
    )
    assert second.confirmed is True
    assert second.inserted is False
    assert second.application_id == first.application_id
    row = _row(tmp_path / "q.db")
    assert row[0] == "applied"
    assert row[2] == second.snap_after
