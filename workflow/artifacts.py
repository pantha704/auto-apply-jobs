"""Private, database-registered storage for workflow artifacts.

Artifact paths are never URLs and this module deliberately provides no static-file
serving integration.  Callers receive redacted bytes unless they explicitly ask
for sensitive content and the database record has first been approved.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePath


class SensitiveAccessDenied(PermissionError):
    """Raised when unapproved sensitive artifact content is requested."""


class ArtifactNotFound(FileNotFoundError):
    """Raised when an artifact metadata record does not exist."""


class ArtifactIntegrityError(OSError):
    """Raised when stored content no longer matches its registered digest."""


@dataclass(frozen=True)
class Artifact:
    id: int
    run_id: str
    attempt_id: int | None
    kind: str
    path: Path
    storage_key: str
    sha256: str
    size_bytes: int
    pii_class: str
    redaction_status: str
    approved_for_sensitive_access: bool
    created_at: str
    retain_until: str | None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


class ArtifactStore:
    """Store artifacts beneath one explicitly allowlisted private root."""

    REDACTED = b"[REDACTED]"

    def __init__(
        self,
        database: str | Path,
        root: str | Path,
        *,
        allowed_roots: Iterable[str | Path],
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.database = Path(database)
        self.root = Path(root).expanduser().resolve(strict=False)
        allowed = tuple(Path(item).expanduser().resolve(strict=False) for item in allowed_roots)
        if not allowed:
            raise ValueError("at least one private storage root must be allowlisted")
        if not any(self._is_within(self.root, candidate) for candidate in allowed):
            raise ValueError("artifact root is not allowlisted")
        self.allowed_roots = allowed
        self.now = now
        self._make_private_directory(self.root)

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _make_private_directory(path: Path) -> None:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path, 0o700)

    def _make_private_parents(self, path: Path) -> None:
        """Create every directory below root privately, rejecting symlinks."""
        relative = path.relative_to(self.root)
        cursor = self.root
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError("symlinks are not permitted in artifact paths")
            cursor.mkdir(mode=0o700, exist_ok=True)
            if not cursor.is_dir():
                raise ValueError("artifact path parent is not a directory")
            os.chmod(cursor, 0o700)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def _safe_path(self, storage_key: str | Path) -> tuple[str, Path]:
        raw = os.fspath(storage_key)
        key_path = PurePath(raw)
        if not raw or key_path.is_absolute() or any(part in ("", ".", "..") for part in key_path.parts):
            raise ValueError("artifact storage key must be a safe relative path")
        candidate = self.root.joinpath(*key_path.parts)
        resolved = candidate.resolve(strict=False)
        if not self._is_within(resolved, self.root):
            raise ValueError("artifact path escapes the private root")
        # resolve() catches existing symlinks; prohibit them even when they point inward.
        cursor = self.root
        for part in key_path.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError("symlinks are not permitted in artifact paths")
        return str(key_path), candidate

    def store(
        self,
        run_id: str,
        storage_key: str | Path,
        content: bytes,
        *,
        kind: str,
        pii_class: str,
        attempt_id: int | None = None,
        retain_until: datetime | None = None,
        redaction_status: str = "pending",
    ) -> Artifact:
        if not isinstance(content, bytes):
            raise TypeError("artifact content must be bytes")
        if not run_id or not kind or not pii_class:
            raise ValueError("run_id, kind, and pii_class are required")
        key, path = self._safe_path(storage_key)
        self._make_private_parents(path.parent)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError as exc:
            raise FileExistsError(f"artifact already exists: {key}") from exc
        try:
            with os.fdopen(descriptor, "wb") as target:
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
            os.chmod(path, 0o600)
            digest = hashlib.sha256(content).hexdigest()
            created_at = _iso(self.now())
            retained = _iso(retain_until) if retain_until is not None else None
            with self._connect() as db:
                cursor = db.execute(
                    """INSERT INTO artifacts
                    (run_id,attempt_id,kind,path,storage_key,sha256,size_bytes,pii_class,
                     redaction_status,approved_for_sensitive_access,created_at,retain_until)
                    VALUES(?,?,?,?,?,?,?,?,?,0,?,?)""",
                    (run_id, attempt_id, kind, str(path), key, digest, len(content),
                     pii_class, redaction_status, created_at, retained),
                )
                if cursor.lastrowid is None:  # pragma: no cover - sqlite always supplies it
                    raise RuntimeError("artifact registration did not return an id")
                artifact_id = cursor.lastrowid
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return Artifact(artifact_id, run_id, attempt_id, kind, path, key, digest,
                        len(content), pii_class, redaction_status, False, created_at, retained)

    # A descriptive alias for callers that prefer verb-object naming.
    store_artifact = store

    def _row(self, artifact_id: int) -> sqlite3.Row:
        with self._connect() as db:
            row = db.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        if row is None:
            raise ArtifactNotFound(f"artifact {artifact_id} does not exist")
        return row

    def approve_sensitive_access(self, artifact_id: int, *, approved: bool = True) -> None:
        with self._connect() as db:
            result = db.execute(
                "UPDATE artifacts SET approved_for_sensitive_access=? WHERE id=?",
                (int(approved), artifact_id),
            )
            if result.rowcount != 1:
                raise ArtifactNotFound(f"artifact {artifact_id} does not exist")

    def read(self, artifact_id: int, *, sensitive: bool = False) -> bytes:
        row = self._row(artifact_id)
        if not sensitive:
            return self.REDACTED
        if not bool(row["approved_for_sensitive_access"]):
            raise SensitiveAccessDenied("sensitive artifact access has not been approved")
        _, expected_path = self._safe_path(row["storage_key"])
        registered_path = Path(row["path"])
        if registered_path != expected_path:
            raise ArtifactIntegrityError("artifact path metadata is inconsistent")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(expected_path, flags)
        with os.fdopen(descriptor, "rb") as source:
            content = source.read()
        if len(content) != row["size_bytes"] or hashlib.sha256(content).hexdigest() != row["sha256"]:
            raise ArtifactIntegrityError("artifact content failed integrity verification")
        return content

    read_artifact = read

    def purge_expired(self, *, before: datetime | None = None) -> int:
        cutoff = _iso(before if before is not None else self.now())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT id,path,storage_key FROM artifacts WHERE retain_until IS NOT NULL AND retain_until<=?",
                (cutoff,),
            ).fetchall()
            purged = 0
            for row in rows:
                _, expected_path = self._safe_path(row["storage_key"])
                if Path(row["path"]) != expected_path:
                    raise ArtifactIntegrityError("refusing to purge inconsistent artifact path")
                expected_path.unlink(missing_ok=True)
                db.execute("DELETE FROM artifacts WHERE id=?", (row["id"],))
                purged += 1
            db.commit()
        return purged
