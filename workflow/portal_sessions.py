from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from cryptography.fernet import Fernet, InvalidToken

_ALLOWED_PORTALS = {
    "linkedin",
    "wellfound",
    "internshala",
    "yc",
    "himalayas",
    "naukri",
}
_PROBE_STATES = {"valid", "expired", "challenged", "unknown"}
_MAX_BUNDLE_BYTES = 5 * 1024 * 1024


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _safe_detail(value: str | None) -> str | None:
    if not value:
        return None
    return " ".join(value.split())[:160]


def _portal(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in _ALLOWED_PORTALS:
        raise ValueError("unsupported portal")
    return normalized


def _validate_state(state: Mapping[str, Any]) -> bytes:
    if not isinstance(state, Mapping):
        raise ValueError("storage state must be an object")
    cookies = state.get("cookies", [])
    origins = state.get("origins", [])
    if not isinstance(cookies, list) or not isinstance(origins, list):
        raise ValueError("storage state cookies and origins must be lists")
    for cookie in cookies:
        if not isinstance(cookie, Mapping):
            raise ValueError("storage state cookie must be an object")
        if not all(isinstance(cookie.get(key), str) and cookie.get(key) for key in ("name", "value", "domain")):
            raise ValueError("storage state cookie is incomplete")
    for origin in origins:
        if not isinstance(origin, Mapping) or not isinstance(origin.get("origin"), str):
            raise ValueError("storage state origin is invalid")
    try:
        encoded = json.dumps(
            {"cookies": cookies, "origins": origins},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("storage state is not JSON serializable") from exc
    if len(encoded) > _MAX_BUNDLE_BYTES:
        raise ValueError("storage state exceeds size limit")
    return encoded


def classify_probe(
    url: str,
    title: str,
    body: str,
    *,
    network_error: bool = False,
) -> str:
    """Classify only strong authentication signals; ambiguity stays unknown."""
    if network_error:
        return "unknown"
    haystack = " ".join((url, title, body[:5000])).lower()
    challenge_markers = (
        "/cdn-cgi/challenge-platform",
        "/checkpoint/",
        "/challenge/",
        "just a moment",
        "verify you are human",
        "security verification",
        "cf-chl-",
    )
    if any(marker in haystack for marker in challenge_markers):
        return "challenged"
    expiry_markers = (
        "/login",
        "/authwall",
        "/signin",
        "/sign-in",
        "sign in to continue",
        "sign in | linkedin",
    )
    if any(marker in haystack for marker in expiry_markers):
        return "expired"
    if url.startswith(("http://", "https://")):
        return "valid"
    return "unknown"


@dataclass(frozen=True)
class RenewalLease:
    portal: str
    owner: str
    token: str
    fencing_token: int
    expires_at: str


@dataclass(frozen=True)
class SessionVersion:
    id: str
    portal: str
    revision: int
    lifecycle: str
    bundle_path: Path


@dataclass(frozen=True)
class SessionSnapshot:
    portal: str
    version_id: str
    revision: int
    state: dict[str, Any]


@dataclass(frozen=True)
class MaterializedSession:
    portal: str
    version_id: str
    revision: int
    path: Path


class PortalSessionManager:
    """Versioned encrypted portal sessions with fenced renewal ownership."""

    def __init__(
        self,
        db_path: str | Path,
        storage_dir: str | Path,
        fernet: Fernet | bytes | str,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.db_path = Path(db_path)
        self.storage_dir = Path(storage_dir)
        if "static" in self.storage_dir.resolve().parts:
            raise ValueError("session storage must be outside static directories")
        self.storage_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if (self.storage_dir.stat().st_mode & 0o777) != 0o700:
            os.chmod(self.storage_dir, 0o700)
        self.fernet = fernet if isinstance(fernet, Fernet) else Fernet(
            fernet.encode() if isinstance(fernet, str) else fernet
        )
        self.clock = clock

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _ensure_portal(self, db: sqlite3.Connection, portal: str) -> None:
        db.execute(
            """INSERT OR IGNORE INTO portal_sessions(portal,state,updated_at)
               VALUES(?,'unknown',?)""",
            (portal, _iso(self.clock())),
        )

    def _require_lease(self, db: sqlite3.Connection, portal: str, token: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM browser_session_leases WHERE session_ref=?",
            (f"portal:{portal}",),
        ).fetchone()
        if row is None or not secrets.compare_digest(row["lease_token"], token):
            raise PermissionError("renewal lease is not owned by caller")
        if _parse(row["expires_at"]) <= self.clock():
            raise PermissionError("renewal lease has expired")
        return row

    def acquire_renewal(self, portal: str, owner: str, *, ttl_seconds: int = 300) -> RenewalLease:
        portal = _portal(portal)
        if not owner.strip() or not 1 <= ttl_seconds <= 1800:
            raise ValueError("invalid renewal lease request")
        now = self.clock()
        expires = now + timedelta(seconds=ttl_seconds)
        token = secrets.token_urlsafe(32)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._ensure_portal(db, portal)
            ref = f"portal:{portal}"
            row = db.execute(
                "SELECT * FROM browser_session_leases WHERE session_ref=?", (ref,)
            ).fetchone()
            if row is not None and _parse(row["expires_at"]) > now:
                raise TimeoutError("portal renewal is already leased")
            fencing = (int(row["fencing_token"]) if row else 0) + 1
            if row is None:
                db.execute(
                    """INSERT INTO browser_session_leases(
                         session_ref,owner_id,run_id,acquired_at,heartbeat_at,expires_at,
                         lease_token,fencing_token
                       ) VALUES(?,?,?,?,?,?,?,?)""",
                    (ref, owner, None, _iso(now), _iso(now), _iso(expires), token, fencing),
                )
            else:
                db.execute(
                    """UPDATE browser_session_leases
                       SET owner_id=?,run_id=NULL,acquired_at=?,heartbeat_at=?,expires_at=?,
                           lease_token=?,fencing_token=? WHERE session_ref=?""",
                    (owner, _iso(now), _iso(now), _iso(expires), token, fencing, ref),
                )
            db.execute(
                "UPDATE portal_sessions SET state='renewing',updated_at=? WHERE portal=?",
                (_iso(now), portal),
            )
            self._event(db, portal, None, "renewal_acquired", "renewing", None)
        return RenewalLease(portal, owner, token, fencing, _iso(expires))

    def heartbeat(self, portal: str, token: str, *, ttl_seconds: int = 300) -> str:
        portal = _portal(portal)
        if not 1 <= ttl_seconds <= 1800:
            raise ValueError("invalid lease ttl")
        now = self.clock()
        expires = now + timedelta(seconds=ttl_seconds)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_lease(db, portal, token)
            db.execute(
                "UPDATE browser_session_leases SET heartbeat_at=?,expires_at=? WHERE session_ref=?",
                (_iso(now), _iso(expires), f"portal:{portal}"),
            )
        return _iso(expires)

    def release_renewal(self, portal: str, token: str) -> None:
        portal = _portal(portal)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_lease(db, portal, token)
            db.execute(
                "DELETE FROM browser_session_leases WHERE session_ref=?",
                (f"portal:{portal}",),
            )
            current = db.execute(
                "SELECT current_version_id FROM portal_sessions WHERE portal=?", (portal,)
            ).fetchone()
            state = "valid" if current and current["current_version_id"] else "unknown"
            db.execute(
                """UPDATE portal_sessions SET state=CASE
                     WHEN state IN ('challenged','expired','operator_required') THEN state ELSE ? END,
                     updated_at=? WHERE portal=?""",
                (state, _iso(self.clock()), portal),
            )
            self._event(db, portal, None, "renewal_released", state, None)

    def _atomic_write(self, portal: str, revision: int, payload: bytes) -> Path:
        portal_dir = self.storage_dir / portal
        portal_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(portal_dir, 0o700)
        destination = portal_dir / f"session-r{revision}.enc"
        fd, temporary = tempfile.mkstemp(prefix=".candidate-", dir=portal_dir)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            os.chmod(destination, 0o600)
            directory_fd = os.open(portal_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return destination
        except Exception:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary)
            raise

    def stage_candidate(self, portal: str, state: Mapping[str, Any], token: str) -> SessionVersion:
        portal = _portal(portal)
        cleartext = _validate_state(state)
        encrypted = self.fernet.encrypt(cleartext)
        now = _iso(self.clock())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_lease(db, portal, token)
            self._ensure_portal(db, portal)
            revision = int(
                db.execute(
                    "SELECT COALESCE(MAX(revision),0)+1 FROM portal_session_versions WHERE portal=?",
                    (portal,),
                ).fetchone()[0]
            )
            version_id = uuid.uuid4().hex
        path = self._atomic_write(portal, revision, encrypted)
        digest = hashlib.sha256(encrypted).hexdigest()
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                self._require_lease(db, portal, token)
                db.execute(
                    """INSERT INTO portal_session_versions(
                         id,portal,revision,lifecycle,bundle_path,bundle_sha256,created_at
                       ) VALUES(?,?,?,'candidate',?,?,?)""",
                    (version_id, portal, revision, str(path), digest, now),
                )
                db.execute(
                    "UPDATE portal_sessions SET state='probing',updated_at=? WHERE portal=?",
                    (now, portal),
                )
                self._event(db, portal, version_id, "candidate_staged", "probing", None)
        except Exception:
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
            raise
        return SessionVersion(version_id, portal, revision, "candidate", path)

    def record_probe(
        self,
        portal: str,
        version_id: str,
        state: str,
        token: str,
        safe_detail: str | None = None,
    ) -> None:
        portal = _portal(portal)
        if state not in _PROBE_STATES:
            raise ValueError("invalid probe state")
        now = _iso(self.clock())
        detail = _safe_detail(safe_detail)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_lease(db, portal, token)
            row = db.execute(
                "SELECT lifecycle FROM portal_session_versions WHERE id=? AND portal=?",
                (version_id, portal),
            ).fetchone()
            if row is None or row["lifecycle"] != "candidate":
                raise ValueError("probe target is not a candidate")
            db.execute(
                """UPDATE portal_session_versions
                   SET probe_state=?,probe_at=?,safe_detail=? WHERE id=?""",
                (state, now, detail, version_id),
            )
            db.execute(
                """UPDATE portal_sessions SET state=?,last_probe_at=?,
                   last_success_at=CASE WHEN ?='valid' THEN ? ELSE last_success_at END,
                   failure_code=CASE WHEN ?='valid' THEN NULL ELSE ? END,
                   safe_detail=?,updated_at=? WHERE portal=?""",
                (state, now, state, now, state, state, detail, now, portal),
            )
            self._event(db, portal, version_id, "probe", state, detail)

    def promote(self, portal: str, version_id: str, token: str) -> SessionVersion:
        portal = _portal(portal)
        now = _iso(self.clock())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_lease(db, portal, token)
            candidate = db.execute(
                "SELECT * FROM portal_session_versions WHERE id=? AND portal=?",
                (version_id, portal),
            ).fetchone()
            if candidate is None or candidate["lifecycle"] != "candidate":
                raise ValueError("version is not a candidate")
            if candidate["probe_state"] != "valid":
                raise ValueError("candidate requires a valid probe before promotion")
            session = db.execute(
                "SELECT current_version_id FROM portal_sessions WHERE portal=?", (portal,)
            ).fetchone()
            previous_id = session["current_version_id"] if session else None
            db.execute(
                "UPDATE portal_session_versions SET lifecycle='rejected' WHERE portal=? AND lifecycle='candidate' AND id<>?",
                (portal, version_id),
            )
            db.execute(
                "UPDATE portal_session_versions SET lifecycle='rejected' WHERE portal=? AND lifecycle='previous'",
                (portal,),
            )
            if previous_id:
                db.execute(
                    "UPDATE portal_session_versions SET lifecycle='previous' WHERE id=?",
                    (previous_id,),
                )
            db.execute(
                "UPDATE portal_session_versions SET lifecycle='current',promoted_at=? WHERE id=?",
                (now, version_id),
            )
            db.execute(
                """UPDATE portal_sessions SET state='valid',current_version_id=?,
                   previous_version_id=?,last_success_at=?,failure_code=NULL,safe_detail=NULL,
                   updated_at=? WHERE portal=?""",
                (version_id, previous_id, now, now, portal),
            )
            self._event(db, portal, version_id, "promoted", "valid", None)
            return SessionVersion(
                candidate["id"], portal, int(candidate["revision"]), "current", Path(candidate["bundle_path"])
            )

    def _load_snapshot_row(self, portal: str, row: sqlite3.Row | None) -> SessionSnapshot:
        if row is None:
            raise RuntimeError("portal session revision is unavailable")
        path = Path(row["bundle_path"])
        encrypted = path.read_bytes()
        if hashlib.sha256(encrypted).hexdigest() != row["bundle_sha256"]:
            raise RuntimeError("portal session bundle failed integrity verification")
        try:
            state = json.loads(self.fernet.decrypt(encrypted).decode("utf-8"))
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("portal session bundle cannot be decrypted") from exc
        _validate_state(state)
        return SessionSnapshot(portal, row["id"], int(row["revision"]), state)

    def load_current(self, portal: str) -> SessionSnapshot:
        portal = _portal(portal)
        with self._connect() as db:
            row = db.execute(
                """SELECT v.* FROM portal_sessions s
                   JOIN portal_session_versions v ON v.id=s.current_version_id
                   WHERE s.portal=?""",
                (portal,),
            ).fetchone()
        return self._load_snapshot_row(portal, row)

    def load_revision(self, portal: str, revision: int) -> SessionSnapshot:
        portal = _portal(portal)
        if revision < 1:
            raise ValueError("revision must be positive")
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM portal_session_versions WHERE portal=? AND revision=?",
                (portal, revision),
            ).fetchone()
        return self._load_snapshot_row(portal, row)

    @contextlib.contextmanager
    def materialize(self, portal: str, runtime_dir: str | Path) -> Iterator[MaterializedSession]:
        portal = _portal(portal)
        if self.public_status(portal)["state"] != "valid":
            raise RuntimeError("portal session is not valid")
        snapshot = self.load_current(portal)
        root = Path(runtime_dir)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(root, 0o700)
        fd, path_text = tempfile.mkstemp(prefix=f"{snapshot.portal}-r{snapshot.revision}-", suffix=".json", dir=root)
        path = Path(path_text)
        try:
            os.fchmod(fd, 0o600)
            data = _validate_state(snapshot.state)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            yield MaterializedSession(snapshot.portal, snapshot.version_id, snapshot.revision, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                path.unlink()

    def record_health(
        self,
        portal: str,
        state: str,
        safe_detail: str | None = None,
    ) -> None:
        """Record a read-only probe of the currently published session."""
        portal = _portal(portal)
        if state not in _PROBE_STATES:
            raise ValueError("invalid probe state")
        now = _iso(self.clock())
        detail = _safe_detail(safe_detail)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._ensure_portal(db, portal)
            db.execute(
                """UPDATE portal_sessions SET state=?,last_probe_at=?,
                   last_success_at=CASE WHEN ?='valid' THEN ? ELSE last_success_at END,
                   failure_code=CASE WHEN ?='valid' THEN NULL ELSE ? END,
                   safe_detail=?,updated_at=? WHERE portal=?""",
                (state, now, state, now, state, state, detail, now, portal),
            )
            self._event(db, portal, None, "health_probe", state, detail)

    def public_status(self, portal: str) -> dict[str, Any]:
        portal = _portal(portal)
        with self._connect() as db:
            row = db.execute(
                """SELECT s.portal,s.state,s.last_probe_at,s.last_success_at,s.failure_code,
                          s.safe_detail,s.updated_at,cv.revision current_revision,
                          pv.revision previous_revision,
                          l.owner_id renewal_owner,l.expires_at lease_expires_at
                   FROM portal_sessions s
                   LEFT JOIN portal_session_versions cv ON cv.id=s.current_version_id
                   LEFT JOIN portal_session_versions pv ON pv.id=s.previous_version_id
                   LEFT JOIN browser_session_leases l ON l.session_ref='portal:'||s.portal
                   WHERE s.portal=?""",
                (portal,),
            ).fetchone()
        if row is None:
            return {
                "portal": portal,
                "state": "unknown",
                "current_revision": None,
                "previous_revision": None,
                "last_probe_at": None,
                "last_success_at": None,
                "failure_code": None,
                "safe_detail": None,
                "renewing": False,
                "lease_expires_at": None,
                "updated_at": None,
            }
        result = dict(row)
        result["renewing"] = bool(result.pop("renewal_owner"))
        return result

    def list_public_statuses(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            portals = [row[0] for row in db.execute("SELECT portal FROM portal_sessions ORDER BY portal")]
        return [self.public_status(portal) for portal in portals]

    def _event(
        self,
        db: sqlite3.Connection,
        portal: str,
        version_id: str | None,
        event: str,
        state: str | None,
        safe_detail: str | None,
    ) -> None:
        db.execute(
            """INSERT INTO portal_session_events(
                 portal,version_id,event,state,safe_detail,created_at
               ) VALUES(?,?,?,?,?,?)""",
            (portal, version_id, event, state, _safe_detail(safe_detail), _iso(self.clock())),
        )
