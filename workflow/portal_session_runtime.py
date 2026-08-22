from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

from .portal_sessions import PortalSessionManager, SessionSnapshot

ROOT = Path(__file__).resolve().parents[1]


class PortalSessionUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=1)
def session_manager() -> PortalSessionManager:
    db_path = Path(os.getenv("JOBHUNT_CONTROL_DB", ROOT / "controlplane.db"))
    key_path = Path(os.getenv("JOBHUNT_VAULT_KEY", ROOT / ".controlplane.key"))
    storage = Path(
        os.getenv("JOBHUNT_SESSION_STORAGE", ROOT / ".private" / "sessions")
    )
    try:
        key = key_path.read_bytes().strip()
        fernet = Fernet(key)
    except (OSError, ValueError) as exc:
        raise PortalSessionUnavailable("portal session vault is unavailable") from exc
    return PortalSessionManager(db_path, storage, fernet)


def current_session(portal: str) -> SessionSnapshot:
    manager = session_manager()
    status = manager.public_status(portal)
    if status["state"] != "valid" or status["current_revision"] is None:
        raise PortalSessionUnavailable(f"{portal} session is not valid")
    try:
        return manager.load_current(portal)
    except (OSError, RuntimeError, ValueError) as exc:
        raise PortalSessionUnavailable(f"{portal} session cannot be loaded") from exc


def inject_current_session(
    context: Any,
    portal: str,
    expected_revision: int | None = None,
) -> int:
    """Inject one immutable revision into an isolated worker context."""
    snapshot = current_session(portal)
    if expected_revision is not None and snapshot.revision != expected_revision:
        raise PortalSessionUnavailable(f"{portal} session revision changed before injection")
    cookies = [cookie for cookie in snapshot.state.get("cookies", []) if cookie.get("name") and cookie.get("value")]
    if cookies:
        context.add_cookies(cookies)
    return snapshot.revision


def clear_cache() -> None:
    session_manager.cache_clear()
