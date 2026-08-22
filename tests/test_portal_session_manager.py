from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet


def legacy_control(path):
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE sites (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          base_url TEXT NOT NULL UNIQUE,
          hostname TEXT NOT NULL,
          adapter TEXT NOT NULL DEFAULT 'auto',
          auth_type TEXT NOT NULL DEFAULT 'none',
          enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE profile_fields (
          field TEXT PRIMARY KEY,
          value_enc BLOB NOT NULL,
          updated_at TEXT NOT NULL
        );
        """
    )
    db.commit()
    db.close()


class Clock:
    def __init__(self):
        self.value = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def manager(tmp_path):
    from workflow.portal_sessions import PortalSessionManager
    from workflow.schema import migrate_control

    db = tmp_path / "control.db"
    legacy_control(db)
    migrate_control(db)
    clock = Clock()
    service = PortalSessionManager(
        db,
        tmp_path / "private_sessions",
        Fernet(Fernet.generate_key()),
        clock=clock,
    )
    return service, db, tmp_path / "private_sessions", clock


def state(cookie_value="secret-cookie"):
    return {
        "cookies": [
            {
                "name": "session",
                "value": cookie_value,
                "domain": ".example.test",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        ],
        "origins": [],
    }


def publish(service, portal, value="secret-cookie", owner="renew-1"):
    lease = service.acquire_renewal(portal, owner, ttl_seconds=30)
    candidate = service.stage_candidate(portal, state(value), lease.token)
    service.record_probe(portal, candidate.id, "valid", lease.token)
    current = service.promote(portal, candidate.id, lease.token)
    service.release_renewal(portal, lease.token)
    return current


def test_manager_does_not_mutate_already_secure_storage_permissions(tmp_path, monkeypatch):
    from workflow.portal_sessions import PortalSessionManager
    from workflow.schema import migrate_control

    db = tmp_path / "control.db"
    storage = tmp_path / "sessions"
    storage.mkdir(mode=0o700)
    legacy_control(db)
    migrate_control(db)

    def reject_chmod(*args, **kwargs):
        raise AssertionError("secure existing storage must not be chmodded")

    monkeypatch.setattr("workflow.portal_sessions.os.chmod", reject_chmod)
    PortalSessionManager(db, storage, Fernet(Fernet.generate_key()))


def test_control_v7_creates_versioned_session_schema(tmp_path):
    from workflow.schema import migrate_control

    path = tmp_path / "control.db"
    legacy_control(path)
    assert migrate_control(path) == [1, 2, 3, 4, 5, 6, 7]
    assert migrate_control(path) == []
    with sqlite3.connect(path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"portal_sessions", "portal_session_versions", "portal_session_events"} <= tables
        cols = {row[1] for row in db.execute("PRAGMA table_info(browser_session_leases)")}
        assert {"lease_token", "fencing_token"} <= cols


def test_candidate_is_encrypted_private_and_database_contains_no_cookie_material(manager):
    service, db_path, root, _ = manager
    lease = service.acquire_renewal("linkedin", "renew-1", ttl_seconds=30)
    candidate = service.stage_candidate("linkedin", state("never-store-plaintext"), lease.token)

    assert (os.stat(root).st_mode & 0o777) == 0o700
    assert (os.stat(candidate.bundle_path).st_mode & 0o777) == 0o600
    encrypted = candidate.bundle_path.read_bytes()
    assert b"never-store-plaintext" not in encrypted
    with pytest.raises((UnicodeDecodeError, json.JSONDecodeError)):
        json.loads(encrypted.decode())
    assert b"never-store-plaintext" not in db_path.read_bytes()


def test_promotion_is_test_before_current_and_retains_previous(manager):
    service, _, _, _ = manager
    first = publish(service, "linkedin", "v1")
    second = publish(service, "linkedin", "v2", owner="renew-2")

    snapshot = service.load_current("linkedin")
    assert snapshot.revision == second.revision == first.revision + 1
    assert snapshot.state["cookies"][0]["value"] == "v2"
    status = service.public_status("linkedin")
    assert status["state"] == "valid"
    assert status["current_revision"] == second.revision
    assert status["previous_revision"] == first.revision
    assert "bundle_path" not in status
    assert "digest" not in status
    assert "lease_token" not in status


def test_challenge_or_failed_candidate_cannot_replace_current(manager):
    service, _, _, _ = manager
    current = publish(service, "himalayas", "known-good")
    lease = service.acquire_renewal("himalayas", "renew-2", ttl_seconds=30)
    candidate = service.stage_candidate("himalayas", state("challenged"), lease.token)
    service.record_probe("himalayas", candidate.id, "challenged", lease.token, "cloudflare")
    with pytest.raises(ValueError, match="valid probe"):
        service.promote("himalayas", candidate.id, lease.token)
    assert service.load_current("himalayas").revision == current.revision
    assert service.public_status("himalayas")["state"] == "challenged"


def test_lease_is_portal_scoped_fenced_and_recoverable_after_expiry(manager):
    service, _, _, clock = manager
    first = service.acquire_renewal("yc", "owner-a", ttl_seconds=10)
    service.acquire_renewal("linkedin", "owner-b", ttl_seconds=10)
    with pytest.raises(TimeoutError):
        service.acquire_renewal("yc", "owner-b", ttl_seconds=10)
    with pytest.raises(PermissionError):
        service.release_renewal("yc", "wrong-token")
    clock.advance(11)
    second = service.acquire_renewal("yc", "owner-b", ttl_seconds=10)
    assert second.fencing_token > first.fencing_token
    with pytest.raises(PermissionError):
        service.release_renewal("yc", first.token)


def test_materialized_worker_copy_is_private_and_removed(manager, tmp_path):
    service, _, _, _ = manager
    publish(service, "wellfound")
    with service.materialize("wellfound", tmp_path / "runtime") as snapshot:
        assert snapshot.path.exists()
        assert (os.stat(snapshot.path).st_mode & 0o777) == 0o600
        assert json.loads(snapshot.path.read_text())["cookies"]
        materialized = snapshot.path
    assert not materialized.exists()


def test_malformed_storage_state_is_rejected_before_write(manager):
    service, _, root, _ = manager
    lease = service.acquire_renewal("linkedin", "renew-1", ttl_seconds=30)
    with pytest.raises(ValueError, match="storage state"):
        service.stage_candidate("linkedin", {"cookies": "not-a-list"}, lease.token)
    assert not list(root.rglob("*.enc"))


def test_inject_rejects_revision_change(monkeypatch):
    from workflow import portal_session_runtime as runtime
    from workflow.portal_sessions import SessionSnapshot

    snapshot = SessionSnapshot(
        portal="yc",
        version_id="v2",
        revision=2,
        state={"cookies": [], "origins": []},
    )
    monkeypatch.setattr(runtime, "current_session", lambda portal: snapshot)

    class Context:
        def add_cookies(self, cookies):
            raise AssertionError("must reject before injection")

    with pytest.raises(runtime.PortalSessionUnavailable, match="revision changed"):
        runtime.inject_current_session(Context(), "yc", expected_revision=1)


def test_probe_classification_distinguishes_challenge_expiry_and_unknown():
    from workflow.portal_sessions import classify_probe

    assert classify_probe("https://site.test/cdn-cgi/challenge-platform", "Just a moment", "verify you are human") == "challenged"
    assert classify_probe("https://site.test/login", "Sign in", "Welcome") == "expired"
    assert classify_probe("https://site.test/account", "Account", "Dashboard") == "valid"
    assert classify_probe("", "", "", network_error=True) == "unknown"
