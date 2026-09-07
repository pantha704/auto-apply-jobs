"""Privacy-safe operational guidance for canonical portal sessions."""
from __future__ import annotations

_DEFAULT_DETAIL = {
    "expired": "authentication-required",
    "challenged": "manual-challenge-resolution-required",
    "unknown": "probe-inconclusive",
    "missing": "renewal-required",
}
_ALLOWED_DETAIL = {
    "authenticated-endpoint-accepted",
    "authentication-required",
    "manual-challenge-resolution-required",
    "challenge-detected",
    "probe-inconclusive",
    "probe-network-error",
    "renewal-required",
}


def watchdog_session_guidance(
    state: str,
    safe_detail: str | None,
    pending: int,
    *,
    portal_label: str = "LINKEDIN",
) -> str | None:
    """Return a metadata-only alert while keeping health states distinct."""
    if pending <= 0 or state == "valid":
        return None
    normalized = state if state in _DEFAULT_DETAIL else "unknown"
    detail = safe_detail if safe_detail in _ALLOWED_DETAIL else _DEFAULT_DETAIL[normalized]
    return f"{portal_label} SESSION {normalized.upper()} — {detail}"


class MonotonicEpoch:
    """Persistent monotonic epoch for session fencing.
    
    F11 fix: Normal lease release deletes renewal row causing fencing values
    to repeat. This class maintains a persistent monotonic counter that
    never decreases, even across lease release/acquire cycles.
    """
    
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
    
    def get_epoch(self, portal: str) -> int:
        """Get current epoch for a portal. Never decreases."""
        import sqlite3
        db = sqlite3.connect(self.db_path, timeout=5)
        try:
            db.execute("PRAGMA busy_timeout=5000")
            row = db.execute(
                "SELECT epoch FROM portal_session_epochs WHERE portal=?",
                (portal,),
            ).fetchone()
            return row[0] if row else 0
        finally:
            db.close()
    
    def advance(self, portal: str) -> int:
        """Advance epoch and return new value. Always increases."""
        import sqlite3
        db = sqlite3.connect(self.db_path, timeout=5)
        try:
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("BEGIN IMMEDIATE")
            
            # Create table if not exists
            db.execute("""
                CREATE TABLE IF NOT EXISTS portal_session_epochs (
                    portal TEXT PRIMARY KEY,
                    epoch INTEGER NOT NULL DEFAULT 0
                )
            """)
            
            # Get current epoch
            row = db.execute(
                "SELECT epoch FROM portal_session_epochs WHERE portal=?",
                (portal,),
            ).fetchone()
            current = row[0] if row else 0
            new_epoch = current + 1
            
            # Upsert
            db.execute(
                "INSERT INTO portal_session_epochs(portal, epoch) VALUES(?, ?) "
                "ON CONFLICT(portal) DO UPDATE SET epoch=excluded.epoch",
                (portal, new_epoch),
            )
            
            db.commit()
            return new_epoch
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


class VersionScopedProbes:
    """Version-scoped probe updates.
    
    F11 fix: Probe updates not version-scoped. This ensures probe state
    is only updated for the specific version that was probed, not globally.
    """
    
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
    
    def record_probe(
        self,
        portal: str,
        version_id: str,
        state: str,
        safe_detail: str | None = None,
    ) -> None:
        """Record probe result scoped to specific version."""
        import sqlite3
        from datetime import datetime, timezone
        
        now = datetime.now(timezone.utc).isoformat()
        db = sqlite3.connect(self.db_path, timeout=5)
        try:
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("BEGIN IMMEDIATE")
            
            # Update version-scoped probe
            db.execute(
                """UPDATE portal_session_versions
                   SET probe_state=?, probe_at=?, safe_detail=?
                   WHERE id=? AND portal=? AND lifecycle='candidate'""",
                (state, now, safe_detail, version_id, portal),
            )
            
            # Update portal_sessions only for state tracking, not version
            db.execute(
                """UPDATE portal_sessions 
                   SET state=?, last_probe_at=?,
                   last_success_at=CASE WHEN ?='valid' THEN ? ELSE last_success_at END,
                   failure_code=CASE WHEN ?='valid' THEN NULL ELSE ? END,
                   safe_detail=?, updated_at=? WHERE portal=?""",
                (state, now, state, now, state, state, safe_detail, now, portal),
            )
            
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
