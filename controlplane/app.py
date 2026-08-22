from __future__ import annotations

import asyncio
import base64
import binascii
import importlib.util
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import subprocess
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import psutil
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from discovery.ingest import apply_batch
from workflow.schema import migrate_control

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"
LOG = logging.getLogger(__name__)
KNOWN_ADAPTERS = {
    "linkedin",
    "wellfound",
    "internshala",
    "yc",
    "greenhouse",
    "lever",
    "ashby",
    "workday",
    "smartrecruiters",
    "generic",
}
WORKER_UNITS = {
    "jobhunt-is@is-w1.service",
    "jobhunt-li@w1.service",
    "jobhunt-li@w2.service",
    "jobhunt-wf@w1.service",
    "jobhunt-wf@w2.service",
    "jobhunt-yc@w1.service",
    "jobhunt-ext@w1.service",
    "jobhunt-email@w1.service",
    "jobhunt-review@r1.service",
}


class Settings(BaseModel):
    queue_db: Path
    control_db: Path
    vault_key: Path
    resume: Path
    resume_storage: Path
    auth_disabled: bool
    username: str
    password: str


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings(
        queue_db=Path(os.getenv("JOBHUNT_QUEUE_DB", ROOT / "apply_queue.db")),
        control_db=Path(os.getenv("JOBHUNT_CONTROL_DB", ROOT / "controlplane.db")),
        vault_key=Path(os.getenv("JOBHUNT_VAULT_KEY", ROOT / ".controlplane.key")),
        resume=Path(os.getenv("JOBHUNT_RESUME", ROOT / "resume.pdf")),
        resume_storage=Path(os.getenv("JOBHUNT_RESUME_STORAGE", ROOT / ".private" / "resumes")),
        auth_disabled=os.getenv("JOBHUNT_DASHBOARD_AUTH_DISABLED", "0") == "1",
        username=os.getenv("JOBHUNT_DASHBOARD_USER", ""),
        password=os.getenv("JOBHUNT_DASHBOARD_PASSWORD", ""),
    )


def connect(path: Path, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    if not readonly:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
    return conn


def initialize() -> None:
    cfg = settings()
    legacy_control = cfg.control_db.is_file()
    if legacy_control:
        with sqlite3.connect(cfg.control_db) as probe:
            existing = {row[0] for row in probe.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        legacy_control = bool(existing & {"sites", "profile_fields"}) and not (
            {"external_sources", "control_flags"} <= existing
        )
    with connect(cfg.control_db) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS sites (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          base_url TEXT NOT NULL UNIQUE,
          hostname TEXT NOT NULL,
          adapter TEXT NOT NULL DEFAULT 'auto',
          auth_type TEXT NOT NULL DEFAULT 'none',
          username_enc BLOB,
          password_enc BLOB,
          session_ref TEXT,
          enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS profile_fields (
          field TEXT PRIMARY KEY,
          value_enc BLOB NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          kind TEXT NOT NULL,
          severity TEXT NOT NULL,
          source TEXT NOT NULL,
          message TEXT NOT NULL,
          details TEXT,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS worker_samples (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          unit TEXT NOT NULL,
          active_state TEXT NOT NULL,
          cpu REAL NOT NULL DEFAULT 0,
          memory_bytes INTEGER NOT NULL DEFAULT 0,
          restarts INTEGER NOT NULL DEFAULT 0,
          sampled_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_samples_unit_time ON worker_samples(unit, sampled_at DESC);
        """)
    if not legacy_control:
        migrate_control(cfg.control_db)
    _vault()
    bootstrap_existing_profile()
    bootstrap_existing_sites()


def bootstrap_existing_sites() -> None:
    if os.getenv("JOBHUNT_SITE_BOOTSTRAP", "1") == "0":
        return
    defaults = [
        (
            "LinkedIn",
            "https://www.linkedin.com/jobs",
            "linkedin",
            "session",
            "li_state.json",
        ),
        (
            "Wellfound",
            "https://wellfound.com/jobs",
            "wellfound",
            "session",
            "portal_wellfound.json",
        ),
        (
            "Internshala",
            "https://internshala.com/jobs",
            "internshala",
            "session",
            "profiles/is_login",
        ),
        (
            "Y Combinator",
            "https://www.workatastartup.com/jobs",
            "yc",
            "session",
            "profiles/yc_cap",
        ),
    ]
    ts = now()
    with connect(settings().control_db) as db:
        for name, url, adapter, auth_type, session_ref in defaults:
            db.execute(
                """INSERT OR IGNORE INTO sites(name,base_url,hostname,adapter,auth_type,session_ref,enabled,created_at,updated_at)
                VALUES(?,?,?,?,?,?,1,?,?)""",
                (
                    name,
                    url,
                    urlparse(url).hostname,
                    adapter,
                    auth_type,
                    session_ref,
                    ts,
                    ts,
                ),
            )


def migrate_queue_indexes() -> None:
    """Explicit maintenance migration; never called by dashboard startup."""
    cfg = settings()
    if not cfg.queue_db.exists():
        return
    with connect(cfg.queue_db) as db:
        tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "jobs" in tables:
            job_columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            if {"portal", "status", "prio"} <= job_columns:
                db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(portal, status, prio DESC)"
                )
            if {"status", "result"} <= job_columns:
                db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_jobs_status_result ON jobs(status, result)"
                )
        if "applications" in tables:
            app_columns = {
                row[1] for row in db.execute("PRAGMA table_info(applications)")
            }
            if {"status", "applied_at"} <= app_columns:
                db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_apps_status_time ON applications(status, applied_at DESC)"
                )


def bootstrap_existing_profile() -> None:
    """Import the existing local profile once; never expose its values through APIs."""
    if os.getenv("JOBHUNT_PROFILE_BOOTSTRAP", "1") == "0":
        return
    with connect(settings().control_db) as db:
        if db.execute("SELECT COUNT(*) FROM profile_fields").fetchone()[0]:
            return
    profile_path = Path(os.getenv("JOBHUNT_PROFILE_MODULE", ROOT / "profile.py"))
    try:
        spec = importlib.util.spec_from_file_location(
            "jobhunt_profile_loader", profile_path
        )
        if spec is None or spec.loader is None:
            return
        existing = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(existing)
        values = {
            "full_name": existing.NAME,
            "email": existing.EMAIL,
            "phone": existing.PHONE,
            "city": existing.CITY,
            "country": os.getenv("JOBHUNT_COUNTRY", "India"),
            "years_experience": float(os.getenv("JOBHUNT_YEARS_EXPERIENCE", "1")),
            "work_authorization": os.getenv("JOBHUNT_WORK_AUTHORIZATION", "India"),
            "sponsorship_required": os.getenv("JOBHUNT_SPONSORSHIP_REQUIRED", "1")
            == "1",
        }
        if not all(
            str(values[k]).strip()
            for k in (
                "full_name",
                "email",
                "phone",
                "city",
                "country",
                "work_authorization",
            )
        ):
            return
    except (ImportError, AttributeError, OSError, RuntimeError, ValueError):
        return
    ts = now()
    with connect(settings().control_db) as db:
        for field, value in values.items():
            db.execute(
                "INSERT OR IGNORE INTO profile_fields(field,value_enc,updated_at) VALUES(?,?,?)",
                (field, encrypt(json.dumps(value)), ts),
            )


def _vault() -> Fernet:
    path = settings().vault_key
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(Fernet.generate_key())
        os.chmod(path, 0o600)
    return Fernet(path.read_bytes().strip())


def encrypt(value: str | None) -> bytes | None:
    return _vault().encrypt(value.encode()) if value else None


def decrypt(value: bytes | None) -> str:
    return _vault().decrypt(value).decode() if value else ""


def mask(value: str) -> str:
    if not value:
        return ""
    if "@" in value:
        local, domain = value.split("@", 1)
        return local[:1] + "•••@" + domain[:1] + "•••"
    return value[:2] + "•••" + value[-1:]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def detect_adapter(hostname: str) -> str:
    h = hostname.lower()
    mappings = {
        "linkedin.com": "linkedin",
        "wellfound.com": "wellfound",
        "internshala.com": "internshala",
        "workatastartup.com": "yc",
        "greenhouse.io": "greenhouse",
        "lever.co": "lever",
        "ashbyhq.com": "ashby",
        "myworkdayjobs.com": "workday",
        "smartrecruiters.com": "smartrecruiters",
    }
    return next(
        (
            adapter
            for domain, adapter in mappings.items()
            if h == domain or h.endswith("." + domain)
        ),
        "unresolved",
    )


class SiteInput(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    base_url: str = Field(min_length=8, max_length=2048)
    auth_type: Literal["none", "password", "session"] = "none"
    username: str | None = Field(default=None, max_length=320)
    password: str | None = Field(default=None, max_length=1024)
    session_ref: str | None = Field(default=None, max_length=500)
    adapter: str = "auto"
    enabled: bool = True

    @field_validator("base_url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an http(s) URL")
        return value.rstrip("/")

    @field_validator("adapter")
    @classmethod
    def adapter_name(cls, value: str) -> str:
        value = value.strip().lower()
        if value != "auto" and value not in KNOWN_ADAPTERS:
            raise ValueError("unsupported adapter")
        return value

    @model_validator(mode="after")
    def validate_auth_material(self):
        self.username = self.username.strip() if self.username else None
        self.password = (
            self.password if self.password and self.password.strip() else None
        )
        self.session_ref = self.session_ref.strip() if self.session_ref else None
        if self.auth_type == "password" and not (self.username and self.password):
            raise ValueError("password authentication requires username and password")
        if self.auth_type == "session" and not self.session_ref:
            raise ValueError("session authentication requires session_ref")
        if self.auth_type == "none":
            self.username = self.password = self.session_ref = None
        return self


class ProfileInput(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=3, max_length=320)
    phone: str = Field(min_length=5, max_length=40)
    city: str = Field(min_length=2, max_length=100)
    country: str = Field(min_length=2, max_length=100)
    years_experience: float = Field(ge=0, le=60)
    work_authorization: str = Field(min_length=2, max_length=120)
    sponsorship_required: bool


def authorized(request: Request) -> bool:
    cfg = settings()
    if cfg.auth_disabled:
        return True
    if not cfg.username or not cfg.password:
        return False
    header = request.headers.get("authorization", "")
    if not header.startswith("Basic "):
        return False
    try:
        username, password = (
            base64.b64decode(header[6:], validate=True).decode().split(":", 1)
        )
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return False
    return secrets.compare_digest(username, cfg.username) and secrets.compare_digest(
        password, cfg.password
    )


def _flag(key: str) -> str | None:
    try:
        with connect(settings().control_db, readonly=True) as db:
            row = db.execute(
                "SELECT value FROM control_flags WHERE key=?", (key,)
            ).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None


def _ingest_token_valid(request: Request) -> bool:
    raw = request.headers.get("x-jobhunt-ingest") or ""
    stored = _flag("ingest_token_sha256")
    if not raw or not stored:
        return False
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return hmac.compare_digest(digest, stored)


async def telemetry_loop() -> None:
    while True:
        try:
            worker_status()
        except Exception:
            LOG.exception("worker telemetry sampling failed")
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize()
    task = asyncio.create_task(telemetry_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Auto Apply Control Plane", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def authentication(request: Request, call_next):
    def secured(response: Response) -> Response:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        return response

    if request.url.path in {"/livez", "/readyz"}:
        return secured(await call_next(request))
    if request.url.path.startswith("/api/ingest/") and _ingest_token_valid(request):
        return secured(await call_next(request))
    cfg = settings()
    if not cfg.auth_disabled and (not cfg.username or not cfg.password):
        return secured(
            Response("dashboard authentication is not configured", status_code=503)
        )
    if not authorized(request):
        return secured(
            Response(
                "authentication required",
                status_code=401,
                headers={"WWW-Authenticate": "Basic"},
            )
        )
    if not cfg.auth_disabled and request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        expected_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if request.headers.get("x-jobhunt-csrf") != "1" or (
            origin and origin.rstrip("/") != expected_origin
        ):
            return secured(Response("cross-site request rejected", status_code=403))
    return secured(await call_next(request))


@app.get("/livez")
def livez() -> dict:
    return {"status": "ok"}


def service_ready() -> bool:
    cfg = settings()
    try:
        with connect(cfg.queue_db, readonly=True) as db:
            queue_tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        with connect(cfg.control_db, readonly=True) as db:
            control_tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        Fernet(cfg.vault_key.read_bytes())
        return {"jobs", "applications"} <= queue_tables and {
            "sites",
            "profile_fields",
        } <= control_tables
    except (binascii.Error, OSError, sqlite3.Error, ValueError):
        LOG.exception("service readiness check failed")
        return False


@app.get("/readyz")
def readyz() -> Response:
    if service_ready():
        return Response('{"status":"ready"}', media_type="application/json")
    return Response(
        '{"status":"unavailable"}', status_code=503, media_type="application/json"
    )


@app.get("/api/health")
def health() -> dict:
    ready = service_ready()
    return {"status": "ok" if ready else "unavailable", "ready": ready}


def queue_summary() -> dict:
    cfg = settings()
    if not cfg.queue_db.exists():
        return {
            "pending": 0,
            "claimed": 0,
            "done": 0,
            "skip": 0,
            "total": 0,
            "by_portal": [],
        }
    with connect(cfg.queue_db, readonly=True) as db:
        rows = [
            dict(row)
            for row in db.execute(
                "SELECT portal,status,COUNT(*) count FROM jobs GROUP BY portal,status ORDER BY portal,status"
            )
        ]
    summary = {"pending": 0, "claimed": 0, "done": 0, "skip": 0}
    for row in rows:
        if row["status"] in summary:
            summary[row["status"]] += row["count"]
    summary["total"] = sum(row["count"] for row in rows)
    summary["by_portal"] = rows
    return summary


def application_summary() -> dict:
    cfg = settings()
    if not cfg.queue_db.exists():
        return {"confirmed": 0, "unconfirmed": 0, "total": 0, "latest_confirmed": None}
    with connect(cfg.queue_db, readonly=True) as db:
        rows = dict(
            db.execute(
                "SELECT status,COUNT(*) FROM applications GROUP BY status"
            ).fetchall()
        )
        latest = db.execute(
            "SELECT MAX(applied_at) FROM applications WHERE status='submitted'"
        ).fetchone()[0]
    return {
        "confirmed": rows.get("submitted", 0),
        "unconfirmed": rows.get("submitted-unconfirmed", 0),
        "total": sum(rows.values()),
        "latest_confirmed": latest,
    }


@app.get("/api/overview")
@app.get("/api/workflow/overview")
def overview() -> dict:
    queue = queue_summary()
    apps = application_summary()
    ready = readiness_data()
    workers = worker_status()
    workers_healthy = bool(workers) and all(
        w["active_state"] == "active" for w in workers
    )
    health_value = "healthy" if ready["ready"] and workers_healthy else "attention"
    return {
        "health": health_value,
        "queue": queue,
        "applications": apps,
        "readiness": ready,
        "workers": workers,
    }


def site_public(row: sqlite3.Row) -> dict:
    adapter = (
        row["adapter"] if row["adapter"] != "auto" else detect_adapter(row["hostname"])
    )
    username = decrypt(row["username_enc"])
    session_ref = row["session_ref"]
    session_path = Path(session_ref) if session_ref else None
    if session_path and not session_path.is_absolute():
        session_path = ROOT / session_path
    auth_ready = (
        row["auth_type"] == "none"
        or (
            row["auth_type"] == "password"
            and bool(row["username_enc"] and row["password_enc"])
        )
        or (
            row["auth_type"] == "session"
            and bool(session_path and session_path.exists())
        )
    )
    return {
        "id": row["id"],
        "name": row["name"],
        "base_url": row["base_url"],
        "hostname": row["hostname"],
        "adapter": adapter,
        "adapter_requested": row["adapter"],
        "auth_type": row["auth_type"],
        "username_masked": mask(username),
        "credential_configured": auth_ready,
        "session_ref_configured": bool(session_ref),
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@app.get("/api/sites")
@app.get("/api/workflow/sites")
def list_sites() -> list[dict]:
    with connect(settings().control_db, readonly=True) as db:
        return [
            site_public(row) for row in db.execute("SELECT * FROM sites ORDER BY name")
        ]


@app.post("/api/sites", status_code=status.HTTP_201_CREATED)
def add_site(item: SiteInput) -> dict:
    parsed = urlparse(item.base_url)
    ts = now()
    try:
        with connect(settings().control_db) as db:
            cur = db.execute(
                """INSERT INTO sites(name,base_url,hostname,adapter,auth_type,username_enc,password_enc,session_ref,enabled,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item.name,
                    item.base_url,
                    parsed.hostname.lower(),
                    item.adapter,
                    item.auth_type,
                    encrypt(item.username),
                    encrypt(item.password),
                    item.session_ref,
                    int(item.enabled),
                    ts,
                    ts,
                ),
            )
            row = db.execute(
                "SELECT * FROM sites WHERE id=?", (cur.lastrowid,)
            ).fetchone()
    except sqlite3.IntegrityError:
        raise HTTPException(409, "site already exists")
    return site_public(row)


@app.delete("/api/sites/{site_id}", status_code=204)
def delete_site(site_id: int):
    with connect(settings().control_db) as db:
        changed = db.execute("DELETE FROM sites WHERE id=?", (site_id,)).rowcount
    if not changed:
        raise HTTPException(404, "site not found")


REQUIRED_PROFILE = (
    "full_name",
    "email",
    "phone",
    "city",
    "country",
    "years_experience",
    "work_authorization",
    "sponsorship_required",
)


@app.put("/api/profile")
def update_profile(item: ProfileInput) -> dict:
    ts = now()
    with connect(settings().control_db) as db:
        for field, value in item.model_dump().items():
            db.execute(
                "INSERT INTO profile_fields(field,value_enc,updated_at) VALUES(?,?,?) ON CONFLICT(field) DO UPDATE SET value_enc=excluded.value_enc, updated_at=excluded.updated_at",
                (field, encrypt(json.dumps(value)), ts),
            )
    return profile_status()


def profile_status() -> dict:
    with connect(settings().control_db, readonly=True) as db:
        present = {row[0] for row in db.execute("SELECT field FROM profile_fields")}
    missing = [field for field in REQUIRED_PROFILE if field not in present]
    return {
        "complete": not missing,
        "completed_fields": len(REQUIRED_PROFILE) - len(missing),
        "total_fields": len(REQUIRED_PROFILE),
        "missing_fields": missing,
        "resume_configured": settings().resume.is_file(),
    }


@app.get("/api/profile/status")
def get_profile_status() -> dict:
    return profile_status()


def readiness_data() -> dict:
    issues = []
    profile = profile_status()
    if not profile["complete"]:
        issues.append(
            {
                "code": "profile_incomplete",
                "severity": "blocking",
                "source": "profile",
                "message": "Complete the required applicant profile.",
                "action": "Open Onboarding and provide the missing fields.",
                "details": {"missing": profile["missing_fields"]},
            }
        )
    if not profile["resume_configured"]:
        issues.append(
            {
                "code": "resume_missing",
                "severity": "blocking",
                "source": "profile",
                "message": "No résumé file is configured.",
                "action": "Set JOBHUNT_RESUME to an existing PDF.",
                "details": {},
            }
        )
    sites = list_sites()
    enabled_sites = [site for site in sites if site["enabled"]]
    if not enabled_sites:
        issues.append(
            {
                "code": "no_enabled_sites",
                "severity": "blocking",
                "source": "sites",
                "message": "No enabled job website is configured.",
                "action": "Add or enable at least one website.",
                "details": {},
            }
        )
    for site in enabled_sites:
        if site["adapter"] == "unresolved":
            issues.append(
                {
                    "code": "adapter_unresolved",
                    "severity": "blocking",
                    "source": site["name"],
                    "message": "This website is not mapped to a supported ATS adapter.",
                    "action": "Select an adapter or run assisted discovery.",
                    "details": {"site_id": site["id"], "hostname": site["hostname"]},
                }
            )
        if site["auth_type"] != "none" and not site["credential_configured"]:
            issues.append(
                {
                    "code": "credentials_missing",
                    "severity": "blocking",
                    "source": site["name"],
                    "message": "Login information is missing.",
                    "action": "Add credentials or an authenticated session reference.",
                    "details": {"site_id": site["id"]},
                }
            )
    return {
        "ready": not any(i["severity"] == "blocking" for i in issues),
        "issues": issues,
        "profile": profile,
        "site_count": len(sites),
        "enabled_site_count": len(enabled_sites),
    }


@app.get("/api/readiness")
def readiness() -> dict:
    return readiness_data()


def record_worker_samples(workers: list[dict]) -> None:
    if not workers:
        return
    sampled = now()
    with connect(settings().control_db) as db:
        latest = db.execute("SELECT MAX(sampled_at) FROM worker_samples").fetchone()[0]
        if (
            latest
            and (
                datetime.now(timezone.utc) - datetime.fromisoformat(latest)
            ).total_seconds()
            < 55
        ):
            return
        db.executemany(
            "INSERT INTO worker_samples(unit,active_state,cpu,memory_bytes,restarts,sampled_at) VALUES(?,?,?,?,?,?)",
            [
                (
                    w["unit"],
                    w["active_state"],
                    w["cpu_percent"],
                    w["memory_bytes"],
                    w["restarts"],
                    sampled,
                )
                for w in workers
            ],
        )
        db.execute(
            "DELETE FROM worker_samples WHERE sampled_at < datetime('now','-30 days')"
        )


def worker_status() -> list[dict]:
    cmd = [
        "/usr/bin/systemctl",
        "show",
        *sorted(WORKER_UNITS),
        "-p",
        "Id",
        "-p",
        "ActiveState",
        "-p",
        "SubState",
        "-p",
        "MainPID",
        "-p",
        "NRestarts",
        "-p",
        "ExecMainStatus",
        "--no-pager",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    workers, current = [], {}
    for line in result.stdout.splitlines() + [""]:
        if not line and current:
            pid = int(current.get("MainPID", "0") or 0)
            cpu = memory = 0
            if pid:
                try:
                    proc = psutil.Process(pid)
                    cpu = proc.cpu_percent()
                    memory = proc.memory_info().rss
                except (psutil.Error, ValueError):
                    pass
            workers.append(
                {
                    "unit": current.get("Id"),
                    "active_state": current.get("ActiveState"),
                    "sub_state": current.get("SubState"),
                    "pid": pid,
                    "restarts": int(current.get("NRestarts", "0") or 0),
                    "exit_status": int(current.get("ExecMainStatus", "0") or 0),
                    "cpu_percent": cpu,
                    "memory_bytes": memory,
                }
            )
            current = {}
        elif "=" in line:
            key, value = line.split("=", 1)
            current[key] = value
    record_worker_samples(workers)
    return workers


def _worker_id_from_unit(unit: str) -> str:
    instance = unit.split("@", 1)[1].removesuffix(".service") if "@" in unit else unit
    prefix = unit.split("@", 1)[0].removeprefix("jobhunt-")
    if prefix == "is":
        return instance if instance.startswith("is-") else f"is-{instance}"
    return {
        "wf": f"wf-{instance}", "li": f"li-{instance}", "yc": f"yc-{instance}",
        "ext": f"ext-{instance}", "email": f"email-{instance}",
        "review": f"rev-{instance}",
    }.get(prefix, instance)


def _worker_telemetry() -> dict[str, dict]:
    path = settings().queue_db
    if not path.exists():
        return {}
    with connect(path, readonly=True) as db:
        tables = {
            row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not {"worker_instances", "worker_events"} <= tables:
            return {}
        runtimes = {
            row["id"]: dict(row)
            for row in db.execute("SELECT * FROM worker_instances")
        }
        for worker_id, runtime in runtimes.items():
            runtime["recent_events"] = [
                dict(row) for row in db.execute(
                    """SELECT event,job_id,outcome_code,safe_detail,created_at
                       FROM worker_events WHERE worker_id=? ORDER BY id DESC LIMIT 8""",
                    (worker_id,),
                )
            ]
            runtime["state_path"] = str(
                Path("state_queue") / str(runtime.get("adapter") or "unknown") / worker_id
            )
    return runtimes


@app.get("/api/workers")
@app.get("/api/workflow/workers")
def workers() -> list[dict]:
    live = worker_status()
    telemetry = _worker_telemetry()
    seen: set[str] = set()
    for worker in live:
        worker_id = _worker_id_from_unit(worker.get("unit") or "")
        worker["worker_id"] = worker_id
        runtime = telemetry.get(worker_id)
        if runtime:
            seen.add(worker_id)
            worker.update({
                "runtime_state": runtime.get("state"),
                "adapter": runtime.get("adapter"),
                "current_job_id": runtime.get("current_job_id"),
                "heartbeat_at": runtime.get("heartbeat_at"),
                "last_success_at": runtime.get("last_success_at"),
                "queue_depth": runtime.get("queue_depth"),
                "safe_detail": runtime.get("safe_detail"),
                "state_path": runtime.get("state_path"),
                "recent_events": runtime.get("recent_events", []),
            })
    for worker_id, runtime in telemetry.items():
        if worker_id in seen:
            continue
        live.append({
            "unit": runtime.get("unit"), "worker_id": worker_id,
            "active_state": "unregistered", "sub_state": "telemetry-only",
            "pid": 0, "restarts": 0, "exit_status": 0,
            "cpu_percent": 0, "memory_bytes": 0,
            "runtime_state": runtime.get("state"), "adapter": runtime.get("adapter"),
            "current_job_id": runtime.get("current_job_id"),
            "heartbeat_at": runtime.get("heartbeat_at"),
            "last_success_at": runtime.get("last_success_at"),
            "queue_depth": runtime.get("queue_depth"),
            "safe_detail": runtime.get("safe_detail"),
            "state_path": runtime.get("state_path"),
            "recent_events": runtime.get("recent_events", []),
        })
    return live


@app.get("/api/workers/{unit}/history")
def worker_history(unit: str, hours: int = Query(24, ge=1, le=720)) -> dict:
    if unit not in WORKER_UNITS:
        raise HTTPException(404, "unknown worker")
    with connect(settings().control_db, readonly=True) as db:
        rows = [
            dict(row)
            for row in db.execute(
                "SELECT active_state,cpu,memory_bytes,restarts,sampled_at FROM worker_samples WHERE unit=? AND sampled_at >= datetime('now', ?) ORDER BY sampled_at",
                (unit, f"-{hours} hours"),
            )
        ]
    uptime = (
        (sum(1 for row in rows if row["active_state"] == "active") / len(rows) * 100)
        if rows
        else None
    )
    return {"unit": unit, "hours": hours, "uptime_percent": uptime, "samples": rows}


@app.post("/api/workers/{unit}/{action}")
def worker_action(unit: str, action: str) -> dict:
    if unit not in WORKER_UNITS or action not in {"start", "stop", "restart"}:
        raise HTTPException(404, "unknown worker or action")
    result = subprocess.run(
        ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", action, unit],
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    if result.returncode:
        raise HTTPException(503, (result.stderr or "worker action failed")[:300])
    return {"ok": True, "unit": unit, "action": action}


@app.get("/api/applications")
def applications(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    portal: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
) -> dict:
    params = (portal, portal, status_filter, status_filter)
    with connect(settings().queue_db, readonly=True) as db:
        total = db.execute(
            "SELECT COUNT(*) FROM applications WHERE (? IS NULL OR portal=?) AND (? IS NULL OR status=?)",
            params,
        ).fetchone()[0]
        rows = db.execute(
            """SELECT id,portal,company,role,applied_at,status
            FROM applications
            WHERE (? IS NULL OR portal=?) AND (? IS NULL OR status=?)
            ORDER BY applied_at DESC LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [dict(row) for row in rows],
    }


@app.get("/api/issues")
def issues() -> dict:
    data = readiness_data()
    cfg = settings()
    operational = []
    if cfg.queue_db.exists():
        with connect(cfg.queue_db, readonly=True) as db:
            rows = db.execute(
                "SELECT portal,result,COUNT(*) count FROM jobs WHERE status='skip' AND result IS NOT NULL GROUP BY portal,result ORDER BY count DESC LIMIT 30"
            ).fetchall()
        operational = [
            {"portal": row["portal"], "reason": row["result"], "count": row["count"]}
            for row in rows
        ]
    return {"blocking": data["issues"], "operational": operational}


class IngestBatchInput(BaseModel):
    source_id: str = Field(min_length=1, max_length=80)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    fetched_at: str | None = None


class ExternalSourceInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=2000)
    kind: Literal["sheet", "csv", "html", "json", "manual"] = "manual"
    category: str | None = Field(default=None, max_length=120)
    owner: Literal["n8n", "api"] = "api"


class ColdContactInput(BaseModel):
    company: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    website: str | None = Field(default=None, max_length=2000)
    role: str | None = Field(default=None, max_length=300)
    requirements: str | None = Field(default=None, max_length=4000)
    source_id: str | None = Field(default=None, max_length=80)


class ColdTemplateInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=10000)
    is_default: bool = False


class ColdDraftInput(BaseModel):
    template_id: str | None = Field(default=None, max_length=80)
    subject: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=10000)


class ColdApproveSendInput(BaseModel):
    confirmed: bool = False


def _valid_contact_email(value: str) -> bool:
    local, separator, domain = value.strip().lower().partition("@")
    return bool(separator and local and "." in domain and not any(ch.isspace() for ch in value))


def _render_cold_template(value: str, contact: sqlite3.Row) -> str:
    rendered = value
    fields = {
        "company": contact["company"] or "",
        "role": contact["role"] or "",
        "website": contact["website"] or "",
        "email": contact["email"] or "",
    }
    for key, replacement in fields.items():
        rendered = rendered.replace("{{" + key + "}}", str(replacement))
    return rendered


@app.get("/api/external-sources")
def external_sources() -> dict:
    with connect(settings().control_db, readonly=True) as db:
        rows = db.execute(
            """SELECT s.id,s.name,s.url,s.kind,s.category,s.status,s.owner,
                      s.last_ingested_at,s.last_error,s.created_at,s.updated_at,
                      COUNT(DISTINCT e.id) entity_count,
                      SUM(CASE WHEN e.routed='apply' THEN 1 ELSE 0 END) routed_apply,
                      SUM(CASE WHEN e.routed='watchlist' THEN 1 ELSE 0 END) routed_watchlist,
                      SUM(CASE WHEN e.routed='cold_email' THEN 1 ELSE 0 END) routed_email,
                      SUM(CASE WHEN e.routed='review' THEN 1 ELSE 0 END) routed_review
               FROM external_sources s LEFT JOIN extracted_entities e ON e.source_id=s.id
               GROUP BY s.id ORDER BY s.updated_at DESC LIMIT 100"""
        ).fetchall()
        route_rows = db.execute(
            "SELECT routed,COUNT(*) count FROM extracted_entities GROUP BY routed"
        ).fetchall()
    return {
        "items": [dict(row) for row in rows],
        "routes": {row["routed"]: row["count"] for row in route_rows},
    }


@app.post("/api/external-sources", status_code=201)
def create_external_source(item: ExternalSourceInput) -> dict:
    source_id, ts = str(uuid.uuid4()), now()
    try:
        with connect(settings().control_db) as db:
            db.execute(
                """INSERT INTO external_sources(
                     id,name,url,kind,category,status,owner,created_at,updated_at
                   ) VALUES(?,?,?,?,?,'queued',?,?,?)""",
                (source_id, item.name.strip(), item.url.strip(), item.kind,
                 item.category, item.owner, ts, ts),
            )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "source URL already exists") from exc
    return {"id": source_id, "status": "queued"}


@app.get("/api/cold-email/contacts")
def cold_email_contacts(status_filter: str | None = Query(None, alias="status")) -> dict:
    with connect(settings().control_db, readonly=True) as db:
        rows = db.execute(
            """SELECT c.id,c.company,c.email,c.website,c.role,c.source_id,c.template_id,
                      c.status,c.draft_subject,c.draft_body,c.drafted_at,c.last_sent_at,
                      c.created_at,c.updated_at,s.name source_name
               FROM cold_contacts c LEFT JOIN external_sources s ON s.id=c.source_id
               WHERE (? IS NULL OR c.status=?)
               ORDER BY COALESCE(c.updated_at,c.created_at) DESC LIMIT 100""",
            (status_filter, status_filter),
        ).fetchall()
        counts = db.execute(
            "SELECT status,COUNT(*) count FROM cold_contacts GROUP BY status"
        ).fetchall()
    return {
        "items": [dict(row) for row in rows],
        "counts": {row["status"]: row["count"] for row in counts},
        "automatic_send_requires_approval": True,
    }


@app.post("/api/cold-email/contacts", status_code=201)
def create_cold_email_contact(item: ColdContactInput) -> dict:
    email = item.email.strip()
    if not _valid_contact_email(email):
        raise HTTPException(422, "valid company email required")
    contact_id, ts = str(uuid.uuid4()), now()
    try:
        with connect(settings().control_db) as db:
            if item.source_id and not db.execute(
                "SELECT 1 FROM external_sources WHERE id=?", (item.source_id,)
            ).fetchone():
                raise HTTPException(404, "source not found")
            db.execute(
                """INSERT INTO cold_contacts(
                     id,company,email,email_norm,website,role,requirements,source_id,status,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,'queued',?,?)""",
                (contact_id, item.company.strip(), email, email.lower(), item.website,
                 item.role, item.requirements, item.source_id, ts, ts),
            )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "contact email already exists") from exc
    return {"id": contact_id, "status": "queued"}


@app.get("/api/cold-email/templates")
def cold_email_templates() -> dict:
    with connect(settings().control_db, readonly=True) as db:
        rows = db.execute(
            "SELECT id,name,subject,body,is_default,created_at,updated_at FROM cold_email_templates ORDER BY is_default DESC,updated_at DESC"
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


@app.post("/api/cold-email/templates", status_code=201)
def create_cold_email_template(item: ColdTemplateInput) -> dict:
    template_id, ts = str(uuid.uuid4()), now()
    with connect(settings().control_db) as db:
        if item.is_default:
            db.execute("UPDATE cold_email_templates SET is_default=0")
        db.execute(
            """INSERT INTO cold_email_templates(id,name,subject,body,is_default,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?)""",
            (template_id, item.name.strip(), item.subject, item.body,
             int(item.is_default), ts, ts),
        )
    return {"id": template_id, "is_default": item.is_default}


@app.post("/api/cold-email/contacts/{contact_id}/draft")
def draft_cold_email(contact_id: str, item: ColdDraftInput) -> dict:
    with connect(settings().control_db) as db:
        contact = db.execute("SELECT * FROM cold_contacts WHERE id=?", (contact_id,)).fetchone()
        if not contact:
            raise HTTPException(404, "contact not found")
        template = None
        if item.template_id:
            template = db.execute(
                "SELECT * FROM cold_email_templates WHERE id=?", (item.template_id,)
            ).fetchone()
            if not template:
                raise HTTPException(404, "template not found")
        elif not (item.subject and item.body):
            template = db.execute(
                "SELECT * FROM cold_email_templates ORDER BY is_default DESC,updated_at DESC LIMIT 1"
            ).fetchone()
        subject_source = item.subject if item.subject is not None else (template["subject"] if template else "")
        body_source = item.body if item.body is not None else (template["body"] if template else "")
        if not subject_source.strip() or not body_source.strip():
            raise HTTPException(422, "template or subject and body required")
        subject = _render_cold_template(subject_source, contact)
        body = _render_cold_template(body_source, contact)
        ts = now()
        template_id = item.template_id or (template["id"] if template else None)
        db.execute(
            """UPDATE cold_contacts SET template_id=?,status='drafted',draft_subject=?,
                      draft_body=?,drafted_at=?,updated_at=? WHERE id=?""",
            (template_id, subject, body, ts, ts, contact_id),
        )
    return {"id": contact_id, "status": "drafted", "subject": subject,
            "body": body}


@app.post("/api/cold-email/contacts/{contact_id}/mark-sent")
def mark_cold_email_sent(contact_id: str) -> dict:
    raise HTTPException(
        status.HTTP_410_GONE,
        "manual send recording retired; approve the exact draft for Gmail API delivery",
    )


@app.post("/api/cold-email/contacts/{contact_id}/approve-send")
def approve_cold_email_send(contact_id: str, item: ColdApproveSendInput) -> dict:
    if not item.confirmed:
        raise HTTPException(409, "explicit send approval required")
    from workflow.cold_email import ColdEmailQueue
    try:
        send_id = ColdEmailQueue(settings().control_db).approve(
            contact_id, approved_by="dashboard-operator"
        )
    except KeyError as exc:
        raise HTTPException(404, "contact not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"id": contact_id, "send_id": send_id, "status": "queued"}


@app.get("/api/cold-email/progress")
def cold_email_progress() -> dict:
    token_path = Path(os.environ.get(
        "JOBHUNT_GOOGLE_TOKEN", "/home/ubuntu/.hermes/google_token.json"
    ))
    with connect(settings().control_db, readonly=True) as db:
        counts = {
            row["status"]: row["count"]
            for row in db.execute(
                "SELECT status,COUNT(*) count FROM cold_email_sends GROUP BY status"
            ).fetchall()
        }
        history = [dict(row) for row in db.execute(
            """SELECT s.id,s.contact_id,c.company,c.email,s.subject,s.status,
                      s.provider_id,s.error,s.attempt_count,s.created_at,s.sent_at,s.updated_at
               FROM cold_email_sends s JOIN cold_contacts c ON c.id=s.contact_id
               ORDER BY COALESCE(s.updated_at,s.created_at) DESC LIMIT 100"""
        ).fetchall()]
    status_path = Path(os.environ.get(
        "JOBHUNT_WORKER_STATE", ROOT / "state_queue"
    )) / "cold-email" / "email-w1" / "status.json"
    worker = {"worker_id": "email-w1", "status": "not_started", "updated_at": None}
    try:
        worker.update(json.loads(status_path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        pass
    return {
        "counts": counts,
        "items": history,
        "provider": {"kind": "gmail_api", "authenticated": token_path.is_file()},
        "source_of_truth": "sqlite",
        "event_projection": "jsonl",
        "event_root": "state_queue/cold-email",
        "worker": worker,
    }


@app.post("/api/ingest/batch")
def ingest_batch(item: IngestBatchInput) -> dict:
    if _flag("ingest_enabled") != "1":
        raise HTTPException(status.HTTP_409_CONFLICT, "ingest_disabled")
    if len(item.entities) > 200:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "too_many_entities")
    cfg = settings()
    try:
        return apply_batch(
            str(cfg.control_db), str(cfg.queue_db), item.source_id, item.entities
        )
    except KeyError:
        raise HTTPException(404, "unknown_source")
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@app.post("/api/ingest/error")
def ingest_error(payload: dict[str, Any]) -> dict:
    source_id = str(payload.get("source_id") or "")
    message = str(payload.get("message") or "ingest_error")[:500]
    if not source_id:
        raise HTTPException(400, "source_id required")
    ts = now()
    with connect(settings().control_db) as db:
        db.execute(
            """UPDATE external_sources
               SET error_count=error_count+1, last_error=?, status=CASE WHEN error_count+1>=3 THEN 'paused' ELSE 'error' END, updated_at=?
               WHERE id=?""",
            (message, ts, source_id),
        )
        db.execute(
            "INSERT INTO events(kind,severity,source,message,created_at) VALUES('ingest','error','n8n',?,?)",
            (message, ts),
        )
    return {"ok": True}


@app.get("/api/events")
def events(limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    with connect(settings().control_db, readonly=True) as db:
        return [
            dict(row)
            for row in db.execute(
                "SELECT * FROM events ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        ]


WORKFLOW_CONTROL_TABLES = {
    "candidate_profiles", "candidate_facts", "resume_versions", "resume_parse_facts",
    "preference_sets", "preference_rules", "answer_entries", "operator_tasks",
}
WORKFLOW_QUEUE_TABLES = {"application_runs", "job_attempts", "workflow_actions"}


def _tables(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    try:
        with connect(path, readonly=True) as db:
            return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except (OSError, sqlite3.Error):
        return set()


def _workflow_available(*, queue: bool = False) -> bool:
    required = WORKFLOW_QUEUE_TABLES if queue else WORKFLOW_CONTROL_TABLES
    path = settings().queue_db if queue else settings().control_db
    return required <= _tables(path)


def _require_workflow(*, queue: bool = False) -> None:
    if not _workflow_available(queue=queue):
        raise HTTPException(503, "workflow database migration is not available")


def _profile_service():
    from workflow.profile_service import ProfileService
    return ProfileService(settings().control_db, settings().resume_storage, _vault())


class ProfileDraftInput(BaseModel):
    facts: dict[str, Any] = Field(min_length=1, max_length=200)
    source_resume_version_id: str | None = Field(default=None, max_length=64)


class ResumeFactReviewInput(BaseModel):
    action: Literal["accepted", "edited", "rejected"]
    value: Any | None = None


class PreferenceRuleInput(BaseModel):
    criterion: str = Field(min_length=1, max_length=200)
    mode: Literal["hard", "soft", "none"]
    operator: str = Field(min_length=1, max_length=30)
    expected: Any
    weight: float = Field(default=0, ge=0)
    unknown_policy: Literal["block", "review", "ignore"] = "block"
    ordinal: int = Field(default=0, ge=0)


class PreferenceSetInput(BaseModel):
    version: int = Field(ge=1)
    rules: list[PreferenceRuleInput] = Field(min_length=1, max_length=200)


class AnswerInput(BaseModel):
    question_key: str = Field(min_length=1, max_length=200)
    answer: Any
    answer_type: str = Field(min_length=1, max_length=50)
    scope: dict[str, Any] = Field(default_factory=dict)
    provenance: str = Field(min_length=1, max_length=100)


class TaskResolutionInput(BaseModel):
    resolution: Literal["resolved", "dismissed"] = "resolved"


@app.get("/api/analytics")
@app.get("/api/workflow/analytics")
def workflow_analytics(
    range_name: str = Query("7d", alias="range"),
    start: datetime | None = None,
    end: datetime | None = None,
    bucket: Literal["hourly", "daily", "weekly", "monthly"] | None = None,
) -> dict:
    if not settings().queue_db.is_file():
        return {"available": False, "range": {"name": range_name}, "timeline": []}
    from workflow.analytics import aggregate_analytics
    try:
        result = aggregate_analytics(settings().queue_db, range_name, bucket=bucket,
                                     custom_start=start, custom_end=end)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    result["available"] = any(result["capabilities"].values())
    return result


@app.get("/api/workflow/profiles")
def workflow_profiles() -> dict:
    if not _workflow_available():
        return {"available": False, "items": []}
    with connect(settings().control_db, readonly=True) as db:
        rows = db.execute("""SELECT p.id,p.revision,p.status,p.created_at,p.approved_at,
            p.source_resume_version_id,count(f.id) fact_count
            FROM candidate_profiles p LEFT JOIN candidate_facts f ON f.profile_id=p.id
            GROUP BY p.id ORDER BY p.revision DESC""").fetchall()
    return {"available": True, "items": [dict(row) for row in rows]}


@app.get("/api/workflow/candidate")
def workflow_candidate() -> dict:
    if not _workflow_available():
        return {"available": False, "complete": False, "missing_fields": ["workflow_control_schema"]}
    summary = _profile_service().readiness_summary()
    with connect(settings().control_db, readonly=True) as db:
        row = db.execute("SELECT id,revision,status,created_at,approved_at,source_resume_version_id FROM candidate_profiles WHERE status='approved' ORDER BY revision DESC LIMIT 1").fetchone()
    return {"available": True, "complete": row is not None,
            "profile": dict(row) if row else None, "missing_fields": summary["missing"]}


@app.post("/api/workflow/profiles", status_code=201)
def create_workflow_profile(item: ProfileDraftInput) -> dict:
    _require_workflow()
    profile_id = _profile_service().create_profile(item.facts, source_resume_version_id=item.source_resume_version_id)
    return {"id": profile_id, "status": "draft"}


@app.get("/api/workflow/profiles/{profile_id}")
def workflow_profile(profile_id: str) -> dict:
    _require_workflow()
    try:
        return _profile_service().get_profile(profile_id)
    except KeyError as exc:
        raise HTTPException(404, "profile not found") from exc


@app.post("/api/workflow/profiles/{profile_id}/approve")
def approve_workflow_profile(profile_id: str) -> dict:
    _require_workflow()
    try:
        _profile_service().approve_profile(profile_id)
    except KeyError as exc:
        raise HTTPException(404, "profile not found") from exc
    return {"id": profile_id, "status": "approved"}


@app.get("/api/workflow/resumes")
def workflow_resumes() -> dict:
    if not _workflow_available():
        return {"available": False, "items": []}
    with connect(settings().control_db, readonly=True) as db:
        rows = db.execute("SELECT id,original_name,media_type,size_bytes,parse_status,parser_name,parser_version,created_at,parsed_at,approved_at,supersedes_id,safe_error FROM resume_versions ORDER BY created_at DESC").fetchall()
    return {"available": True, "items": [dict(row) for row in rows]}


def _multipart_file(content_type: str, body: bytes) -> tuple[str, str | None, bytes]:
    """Extract the single `file` part without requiring an optional parser package."""
    if not content_type.lower().startswith("multipart/form-data") or "boundary=" not in content_type:
        raise ValueError("multipart/form-data with a boundary is required")
    boundary = content_type.split("boundary=", 1)[1].strip().strip('"')
    if not boundary or len(boundary) > 200:
        raise ValueError("invalid multipart boundary")
    marker = b"--" + boundary.encode("ascii", "strict")
    for part in body.split(marker):
        if b"\r\n\r\n" not in part:
            continue
        header_blob, payload = part.split(b"\r\n\r\n", 1)
        headers = header_blob.decode("latin-1").lower()
        if 'name="file"' not in headers:
            continue
        original_headers = header_blob.decode("latin-1")
        filename = ""
        disposition = next(
            (line for line in original_headers.split("\r\n") if line.lower().startswith("content-disposition:")),
            "",
        )
        for token in disposition.split(";"):
            if token.strip().lower().startswith("filename="):
                filename = token.split("=", 1)[1].strip().strip('"')
        media_type = None
        for line in original_headers.split("\r\n"):
            if line.lower().startswith("content-type:"):
                media_type = line.split(":", 1)[1].strip()
        return filename, media_type, payload.removesuffix(b"\r\n").removesuffix(b"--")
    raise ValueError("multipart request must contain one file field")


@app.post("/api/workflow/resumes", status_code=201)
async def upload_workflow_resume(request: Request) -> dict:
    _require_workflow()
    limit = 5 * 1024 * 1024
    try:
        body = await request.body()
        if len(body) > limit + 64 * 1024:
            raise ValueError(f"multipart request exceeds {limit} byte file limit")
        filename, media_type, data = _multipart_file(request.headers.get("content-type", ""), body)
        resume_id = _profile_service().upload_resume(filename, data, media_type)
    except (UnicodeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"id": resume_id, "parse_status": "pending"}


@app.post("/api/workflow/resumes/{resume_id}/parse")
def parse_workflow_resume(resume_id: str) -> dict:
    _require_workflow()
    try:
        facts = _profile_service().parse_resume(resume_id)
    except KeyError as exc:
        raise HTTPException(404, "resume not found") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"id": resume_id, "facts": facts}


@app.get("/api/workflow/resumes/{resume_id}/facts")
def workflow_resume_facts(resume_id: str) -> dict:
    _require_workflow()
    return {"id": resume_id, "facts": _profile_service().list_parse_facts(resume_id)}


@app.put("/api/workflow/resume-facts/{fact_id}")
def review_workflow_resume_fact(fact_id: int, item: ResumeFactReviewInput) -> dict:
    _require_workflow()
    try:
        _profile_service().review_parse_fact(fact_id, item.action, item.value)
    except KeyError as exc:
        raise HTTPException(404, "resume fact not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"id": fact_id, "action": item.action}


@app.post("/api/workflow/resumes/{resume_id}/approve")
def approve_workflow_resume(resume_id: str) -> dict:
    _require_workflow()
    try:
        profile_id = _profile_service().approve_resume(resume_id)
    except KeyError as exc:
        raise HTTPException(404, "resume not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"id": resume_id, "status": "approved", "profile_id": profile_id}


@app.get("/api/workflow/preferences")
def workflow_preferences() -> dict:
    if not _workflow_available():
        return {"available": False, "items": []}
    with connect(settings().control_db, readonly=True) as db:
        sets = [dict(r) for r in db.execute("SELECT id,version,status,created_at,activated_at FROM preference_sets ORDER BY version DESC")]
        for item in sets:
            item["rules"] = [dict(r) for r in db.execute("SELECT id,criterion,mode,operator,expected_json,weight,unknown_policy,ordinal FROM preference_rules WHERE preference_set_id=? ORDER BY ordinal", (item["id"],))]
    return {"available": True, "items": sets}


@app.post("/api/workflow/preferences", status_code=201)
def create_workflow_preferences(item: PreferenceSetInput) -> dict:
    _require_workflow()
    from workflow.preferences import PreferenceRepository
    try:
        result = PreferenceRepository(settings().control_db).create_set(version=item.version, rules=[r.model_dump() for r in item.rules])
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "preference version already exists") from exc
    return {"id": result.id, "version": result.version, "status": result.status}


@app.post("/api/workflow/preferences/{version}/activate")
def activate_workflow_preferences(version: int) -> dict:
    _require_workflow()
    from workflow.preferences import PreferenceRepository
    try:
        result = PreferenceRepository(settings().control_db).activate(version)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"id": result.id, "version": result.version, "status": result.status}


def _answer_public(row: sqlite3.Row, *, include_answer: bool = False) -> dict:
    result = {key: row[key] for key in ("id", "question_key", "answer_type", "version", "status", "provenance", "created_at", "approved_at")}
    result["scope"] = json.loads(row["scope_json"])
    if include_answer:
        result["answer"] = json.loads(decrypt(row["answer_enc"]))
    return result


@app.get("/api/workflow/answer-bank")
@app.get("/api/workflow/answers")
def workflow_answers() -> dict:
    if not _workflow_available():
        return {"available": False, "items": []}
    with connect(settings().control_db, readonly=True) as db:
        rows = db.execute("SELECT * FROM answer_entries ORDER BY created_at DESC").fetchall()
    return {"available": True, "items": [_answer_public(row) for row in rows]}


@app.post("/api/workflow/answers", status_code=201)
def create_workflow_answer(item: AnswerInput) -> dict:
    _require_workflow()
    answer_id, created = uuid.uuid4().hex, now()
    with connect(settings().control_db) as db:
        version = db.execute("SELECT coalesce(max(version),0)+1 FROM answer_entries WHERE question_key=?", (item.question_key,)).fetchone()[0]
        db.execute("INSERT INTO answer_entries(id,question_key,answer_enc,answer_type,scope_json,version,status,provenance,created_at) VALUES(?,?,?,?,?,?,'draft',?,?)", (answer_id, item.question_key, encrypt(json.dumps(item.answer)), item.answer_type, json.dumps(item.scope, separators=(",", ":"), sort_keys=True), version, item.provenance, created))
    return {"id": answer_id, "question_key": item.question_key, "version": version, "status": "draft"}


@app.get("/api/workflow/answers/{answer_id}")
def workflow_answer(answer_id: str) -> dict:
    _require_workflow()
    with connect(settings().control_db, readonly=True) as db:
        row = db.execute("SELECT * FROM answer_entries WHERE id=?", (answer_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "answer not found")
    return _answer_public(row, include_answer=True)


@app.put("/api/workflow/answers/{answer_id}")
def update_workflow_answer(answer_id: str, item: AnswerInput) -> dict:
    _require_workflow()
    with connect(settings().control_db) as db:
        changed = db.execute("UPDATE answer_entries SET question_key=?,answer_enc=?,answer_type=?,scope_json=?,provenance=? WHERE id=? AND status='draft'", (item.question_key, encrypt(json.dumps(item.answer)), item.answer_type, json.dumps(item.scope, separators=(",", ":"), sort_keys=True), item.provenance, answer_id)).rowcount
    if not changed:
        raise HTTPException(409, "only an existing draft answer can be edited")
    return {"id": answer_id, "status": "draft"}


@app.delete("/api/workflow/answers/{answer_id}", status_code=204)
def delete_workflow_answer(answer_id: str) -> None:
    _require_workflow()
    with connect(settings().control_db) as db:
        changed = db.execute("DELETE FROM answer_entries WHERE id=? AND status='draft'", (answer_id,)).rowcount
    if not changed:
        raise HTTPException(409, "only an existing draft answer can be deleted")


@app.post("/api/workflow/answers/{answer_id}/approve")
def approve_workflow_answer(answer_id: str) -> dict:
    _require_workflow()
    with connect(settings().control_db) as db:
        row = db.execute("SELECT question_key FROM answer_entries WHERE id=?", (answer_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "answer not found")
        db.execute("UPDATE answer_entries SET status='retired' WHERE question_key=? AND status='approved' AND id<>?", (row[0], answer_id))
        db.execute("UPDATE answer_entries SET status='approved',approved_at=? WHERE id=?", (now(), answer_id))
    return {"id": answer_id, "status": "approved"}


@app.get("/api/workflow/review-tasks")
@app.get("/api/workflow/tasks")
def workflow_tasks(status_filter: str | None = Query(None, alias="status")) -> dict:
    if not _workflow_available():
        return {"available": False, "items": []}
    with connect(settings().control_db, readonly=True) as db:
        rows = db.execute("SELECT id,run_id,site_id,type,status,safe_summary,artifact_id,created_at,resolved_at FROM operator_tasks WHERE (? IS NULL OR status=?) ORDER BY created_at DESC", (status_filter, status_filter)).fetchall()
    return {"available": True, "items": [dict(row) for row in rows]}


@app.post("/api/workflow/tasks/{task_id}/resolve")
def resolve_workflow_task(task_id: str, item: TaskResolutionInput) -> dict:
    _require_workflow()
    with connect(settings().control_db) as db:
        changed = db.execute("UPDATE operator_tasks SET status=?,resolved_at=? WHERE id=? AND status='open'", (item.resolution, now(), task_id)).rowcount
    if not changed:
        raise HTTPException(404, "open task not found")
    return {"id": task_id, "status": item.resolution}


@app.get("/api/workflow/runs")
def workflow_runs(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> dict:
    if not _workflow_available(queue=True):
        return {"available": False, "total": 0, "items": []}
    with connect(settings().queue_db, readonly=True) as db:
        total = db.execute("SELECT count(*) FROM application_runs").fetchone()[0]
        rows = db.execute("SELECT id,job_id,site_id,adapter,recipe_id,recipe_version,candidate_profile_id,resume_version_id,preference_set_id,site_manifest_version,worker_id,state,started_at,finished_at,confirmed,outcome_code,safe_detail FROM application_runs ORDER BY started_at DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return {"available": True, "total": total, "limit": limit, "offset": offset, "items": [dict(row) for row in rows]}


@app.get("/api/workflow/runs/{run_id}/trace")
@app.get("/api/workflow/runs/{run_id}")
def workflow_run_trace(run_id: str) -> dict:
    _require_workflow(queue=True)
    with connect(settings().queue_db, readonly=True) as db:
        run = db.execute("SELECT id,job_id,site_id,adapter,recipe_id,recipe_version,candidate_profile_id,resume_version_id,preference_set_id,site_manifest_version,worker_id,state,started_at,finished_at,confirmed,outcome_code,safe_detail FROM application_runs WHERE id=?", (run_id,)).fetchone()
        if run is None:
            raise HTTPException(404, "run not found")
        attempts = [dict(row) for row in db.execute("SELECT id,run_id,attempt_no,started_at,finished_at,outcome_code,retryable,safe_detail FROM job_attempts WHERE run_id=? ORDER BY attempt_no", (run_id,))]
        for attempt in attempts:
            attempt["actions"] = [dict(row) for row in db.execute("SELECT id,run_id,attempt_id,ordinal,action_type,intent,target_ref,input_ref,source,precondition_json,postcondition_json,status,started_at,finished_at,safe_detail FROM workflow_actions WHERE attempt_id=? ORDER BY ordinal", (attempt["id"],))]
    result = dict(run)
    result["attempts"] = attempts
    return result


@app.get("/api/workflow/readiness")
def workflow_readiness() -> dict:
    control_available = _workflow_available()
    queue_available = _workflow_available(queue=True)
    if not control_available:
        return {"available": False, "ready": False, "missing": ["workflow_control_schema"], "queue_available": queue_available}
    summary = _profile_service().readiness_summary()
    missing = list(summary["missing"])
    if not queue_available:
        missing.append("workflow_queue_schema")
    with connect(settings().control_db, readonly=True) as db:
        preferences = db.execute("SELECT id FROM preference_sets WHERE status='active' LIMIT 1").fetchone()
        open_tasks = db.execute("SELECT count(*) FROM operator_tasks WHERE status='open'").fetchone()[0]
    if preferences is None:
        missing.append("active_preferences")
    return {"available": True, "ready": not missing, "missing": missing,
            "approved_profile_id": summary["approved_profile_id"],
            "approved_resume_id": summary["approved_resume_id"],
            "active_preference_set_id": preferences[0] if preferences else None,
            "open_operator_tasks": open_tasks, "queue_available": queue_available}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
