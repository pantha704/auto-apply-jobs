from __future__ import annotations

import asyncio
import base64
import binascii
import importlib.util
import json
import logging
import os
import secrets
import sqlite3
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import psutil
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

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
    "jobhunt-review@r1.service",
}


class Settings(BaseModel):
    queue_db: Path
    control_db: Path
    vault_key: Path
    resume: Path
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


@app.get("/api/workers")
def workers() -> list[dict]:
    return worker_status()


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


@app.get("/api/events")
def events(limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    with connect(settings().control_db, readonly=True) as db:
        return [
            dict(row)
            for row in db.execute(
                "SELECT * FROM events ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        ]


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
