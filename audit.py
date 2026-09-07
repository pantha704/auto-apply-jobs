#!/usr/bin/env python3
"""Shared audit module for job-hunt workers.
- applications table: company, role, URL, timestamp, answers submitted, resume used, status
- per-application snapshots (screenshot + HTML before/after submit)
- aggressive dedup (unique canonical URL per portal)
"""
import json, os, re, sqlite3, time, hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from submission_signals import has_submission_confirmation

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.getenv("JOBHUNT_QUEUE_DB", os.path.join(HERE, "apply_queue.db"))
AUDIT = os.getenv("JOBHUNT_AUDIT_DIR", os.path.join(HERE, "audits"))

def canonical(url):
    if not url: return ""
    u = url.strip()
    u = re.sub(r"(\?|&)(trackingId|refId|trk|utm_[a-z]+|from|position|pageNum|gclid|fbclid|ref|lipi|currentJobId|originalSubdomain|shareId|searchId|secondarySharedUrl)[^&]*", "", u)
    u = re.sub(r"[?&]+$", "", u)
    return u.rstrip("/")

def db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        portal TEXT NOT NULL,
        company TEXT, role TEXT, url TEXT NOT NULL,
        applied_at TEXT NOT NULL,
        answers TEXT, resume_used TEXT,
        status TEXT, note TEXT,
        snap_before TEXT, snap_after TEXT,
        url_hash TEXT,
        UNIQUE(portal, url_hash)
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_apps_portal_time ON applications(portal, applied_at)")
    c.commit()
    return c

def record_application(portal, company, role, url, status, answers=None, resume_used=None,
                       note="", snap_before=None, snap_after=None):
    """Record an application attempt. Returns (True, id) if inserted, (False, None) if duplicate."""
    c = db()
    uh = hashlib.sha1(canonical(url).encode()).hexdigest()
    # aggressive dedup: same canonical URL = same role
    exists = c.execute("SELECT id, status FROM applications WHERE portal=? AND url_hash=?", (portal, uh)).fetchone()
    if exists:
        if exists[1] == "submit-unconfirmed" and status in {"applied", "submitted"}:
            now = datetime.now(timezone.utc).isoformat()
            c.execute(
                """UPDATE applications SET status=?, applied_at=?, answers=?, resume_used=?,
                   note=?, snap_before=COALESCE(?, snap_before), snap_after=? WHERE id=?""",
                (
                    status,
                    now,
                    json.dumps(answers, ensure_ascii=False) if answers else None,
                    resume_used,
                    note,
                    snap_before,
                    snap_after,
                    exists[0],
                ),
            )
            c.commit()
            rid = exists[0]
            c.close()
            return (False, rid)
        c.close()
        return (False, exists[0])
    now = datetime.now(timezone.utc).isoformat()
    try:
        cur = c.execute("""INSERT INTO applications
        (portal, company, role, url, applied_at, answers, resume_used, status, note, snap_before, snap_after, url_hash)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (portal, company, role, url, now,
         json.dumps(answers, ensure_ascii=False) if answers else None,
         resume_used, status, note, snap_before, snap_after, uh))
        c.commit()
        rid = cur.lastrowid
    except sqlite3.IntegrityError:
        c.rollback()
        c.close()
        return (False, None)
    c.close()
    return (True, rid)

def snapshot(page, portal, job_id, stage):
    """Save screenshot + HTML for audit. Returns paths dict."""
    d = os.path.join(AUDIT, portal, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    os.makedirs(d, exist_ok=True)
    if (os.stat(d).st_mode & 0o777) != 0o700:
        os.chmod(d, 0o700)
    parent = os.path.dirname(d)
    if (os.stat(parent).st_mode & 0o777) != 0o700:
        os.chmod(parent, 0o700)
    root = os.path.abspath(AUDIT)
    if os.path.isdir(root) and (os.stat(root).st_mode & 0o777) != 0o700:
        os.chmod(root, 0o700)
    safe = re.sub(r"[^a-zA-Z0-9_-]", "", (job_id or "job")[:40])
    ts = int(time.time())
    base = os.path.join(d, f"{safe}_{ts}_{stage}")
    try:
        page.screenshot(path=base + ".png", full_page=False)
        os.chmod(base + ".png", 0o600)
    except Exception:
        base_png = None
    else:
        base_png = base + ".png"
    try:
        html = page.content()
        with open(base + ".html", "w") as handle:
            handle.write(html)
        os.chmod(base + ".html", 0o600)
    except Exception:
        html_path = None
    else:
        html_path = base + ".html"
    return {"png": base_png, "html": html_path}


@dataclass(frozen=True)
class SubmissionProof:
    confirmed: bool
    status: str
    snap_before: str | None
    snap_after: str | None
    inserted: bool
    application_id: int | None


def _png_ok(path: str | None) -> bool:
    return bool(path and os.path.isfile(path) and os.path.getsize(path) > 0)


def record_submission_proof(
    page,
    *,
    portal: str,
    job_id: str,
    company: str,
    role: str,
    url: str,
    status_if_confirmed: str,
    resume_used=None,
    answers=None,
    http_statuses=(),
    already_before=None,
    note: str = "",
) -> SubmissionProof:
    before = already_before or snapshot(page, portal, job_id, "before_submit")
    try:
        after_text = page.inner_text("body")
    except Exception:
        after_text = ""
    after = snapshot(page, portal, job_id, "after_submit")
    text_ok = has_submission_confirmation(after_text, http_statuses)
    after_png = after.get("png") if after else None
    png_ok = _png_ok(after_png)
    if png_ok:
        os.chmod(after_png, 0o600)
    confirmed = bool(text_ok and png_ok)
    status = status_if_confirmed if confirmed else "submit-unconfirmed"
    inserted, rid = record_application(
        portal,
        company,
        role,
        url,
        status,
        answers=answers,
        resume_used=resume_used,
        note=note,
        snap_before=(before or {}).get("png"),
        snap_after=after_png if png_ok else None,
    )
    return SubmissionProof(
        confirmed=confirmed,
        status=status,
        snap_before=(before or {}).get("png"),
        snap_after=after_png if png_ok else None,
        inserted=inserted,
        application_id=rid,
    )

class SubmissionFinalizer:
    """Atomically update jobs status AND write audit record in one transaction.

    This replaces the two-step pattern of mark() + audit.record_application()
    which could disagree if one succeeded and the other failed.

    Usage:
        finalizer = SubmissionFinalizer(QUEUE_DB)
        finalizer.finalize(
            job_id=job["id"],
            status="done", result="submitted",
            portal="linkedin", company="Acme", role="Engineer",
            url="https://...", audit_status="submitted",
            answers=answers, resume_used="resume.pdf",
            note="applied", snap_before=..., snap_after=...,
        )
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or DB

    def finalize(
        self,
        *,
        job_id: str | int,
        status: str,
        result: str = "",
        portal: str,
        company: str = "",
        role: str = "",
        url: str = "",
        audit_status: str,
        answers=None,
        resume_used=None,
        note: str = "",
        snap_before=None,
        snap_after=None,
    ) -> None:
        """Atomically update queue status and write audit record."""
        c = sqlite3.connect(self._db_path, timeout=10)
        try:
            c.execute("BEGIN IMMEDIATE")
            # 1. Update jobs table (replaces mark())
            c.execute(
                "UPDATE jobs SET status=?, result=? WHERE id=?",
                (status, result[:200], job_id),
            )
            # 2. Write audit record to applications table
            uh = hashlib.sha1(canonical(url).encode()).hexdigest() if url else ""
            exists = c.execute(
                "SELECT id, status FROM applications WHERE portal=? AND url_hash=?",
                (portal, uh),
            ).fetchone() if uh else None
            now = datetime.now(timezone.utc).isoformat()
            if exists:
                if exists[1] == "submit-unconfirmed" and audit_status in {
                    "applied",
                    "submitted",
                }:
                    c.execute(
                        """UPDATE applications SET status=?, applied_at=?,
                           answers=?, resume_used=?, note=?,
                           snap_before=COALESCE(?, snap_before),
                           snap_after=? WHERE id=?""",
                        (
                            audit_status,
                            now,
                            json.dumps(answers, ensure_ascii=False)
                            if answers
                            else None,
                            resume_used,
                            note,
                            snap_before,
                            snap_after,
                            exists[0],
                        ),
                    )
            elif url:
                try:
                    c.execute(
                        """INSERT INTO applications
                        (portal, company, role, url, applied_at, answers,
                         resume_used, status, note, snap_before, snap_after, url_hash)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            portal,
                            company,
                            role,
                            url,
                            now,
                            json.dumps(answers, ensure_ascii=False)
                            if answers
                            else None,
                            resume_used,
                            audit_status,
                            note,
                            snap_before,
                            snap_after,
                            uh,
                        ),
                    )
                except sqlite3.IntegrityError:
                    pass  # duplicate — acceptable
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()


# F01 fix: Hardcoded PROFILE dict removed. Workers MUST use
# workflow.application_gate.published_runtime() and workflow.answers.AnswerRepository
# for all candidate facts. The old PROFILE dict allowed workers to bypass the
# approved release bundle, leading to incorrect submitted answers.
#
# If you need candidate facts, use:
#   from workflow.application_gate import published_runtime
#   runtime = published_runtime(portal, required_fact_groups)
#   runtime.fact("name")  # approved fact
#
# Legacy code that references audit.PROFILE will get a clear ImportError.
