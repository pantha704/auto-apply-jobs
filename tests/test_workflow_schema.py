from __future__ import annotations

import sqlite3


def legacy_queue(path):
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE jobs (
          id TEXT PRIMARY KEY,
          portal TEXT,
          url TEXT,
          title TEXT,
          source TEXT,
          status TEXT DEFAULT 'pending',
          claimed_by TEXT,
          result TEXT,
          prio INTEGER DEFAULT 0,
          posted_at TEXT,
          fetched_at TEXT
        );
        CREATE TABLE applications (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          portal TEXT NOT NULL,
          company TEXT,
          role TEXT,
          url TEXT NOT NULL,
          applied_at TEXT NOT NULL,
          status TEXT,
          url_hash TEXT,
          UNIQUE(portal, url_hash)
        );
        INSERT INTO jobs(id, portal, url, title, status)
        VALUES('job-1', 'generic', 'https://example.test/jobs/1', 'Engineer', 'pending');
        INSERT INTO applications(portal, url, applied_at, status, url_hash)
        VALUES('generic', 'https://example.test/jobs/0', '2026-08-19T00:00:00Z', 'submitted', 'hash-0');
        """
    )
    db.commit()
    db.close()


def test_queue_migration_is_versioned_idempotent_and_preserves_legacy_rows(tmp_path):
    from workflow.schema import migrate_queue

    path = tmp_path / "queue.db"
    legacy_queue(path)

    assert migrate_queue(path) == [1, 2, 3]
    assert migrate_queue(path) == []

    db = sqlite3.connect(path)
    columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
    assert {
        "claimed_at",
        "lease_expires_at",
        "attempt_count",
        "next_attempt_at",
        "last_outcome_code",
    } <= columns
    assert db.execute("SELECT id, status FROM jobs").fetchall() == [("job-1", "pending")]
    assert db.execute("SELECT status FROM applications").fetchall() == [("submitted",)]
    assert db.execute(
        "SELECT version FROM schema_migrations WHERE database_name='queue'"
    ).fetchall() == [(1,), (2,), (3,)]
    db.close()


def test_queue_migration_creates_operational_history_tables(tmp_path):
    from workflow.schema import migrate_queue

    path = tmp_path / "queue.db"
    legacy_queue(path)
    migrate_queue(path)

    db = sqlite3.connect(path)
    tables = {
        row[0]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {
        "application_runs",
        "job_attempts",
        "worker_instances",
        "worker_events",
        "artifacts",
        "preference_evaluations",
        "workflow_actions",
        "recipes",
        "metric_rollups",
    } <= tables
    assert {
        "job_id",
        "site_id",
        "adapter",
        "state",
        "started_at",
        "finished_at",
        "confirmed",
        "outcome_code",
        "lease_token",
        "candidate_profile_id",
        "resume_version_id",
        "preference_set_id",
        "site_manifest_version",
    } <= {row[1] for row in db.execute("PRAGMA table_info(application_runs)")}
    assert {
        "run_id",
        "attempt_no",
        "started_at",
        "finished_at",
        "outcome_code",
        "retryable",
        "safe_detail",
    } <= {row[1] for row in db.execute("PRAGMA table_info(job_attempts)")}
    assert {"current_job_id", "started_at", "last_event_at"} <= {
        row[1] for row in db.execute("PRAGMA table_info(worker_instances)")
    }
    db.close()


def test_queue_migration_rolls_back_as_one_transaction(tmp_path):
    import pytest

    from workflow.schema import migrate_queue

    path = tmp_path / "queue.db"
    legacy_queue(path)
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE application_runs(id TEXT)")
    db.commit()
    db.close()

    with pytest.raises(sqlite3.OperationalError):
        migrate_queue(path)

    db = sqlite3.connect(path)
    columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
    assert "lease_expires_at" not in columns
    tables = {
        row[0]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    versions = (
        db.execute(
            "SELECT version FROM schema_migrations WHERE database_name='queue'"
        ).fetchall()
        if "schema_migrations" in tables
        else []
    )
    assert versions == []
    db.close()


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
        INSERT INTO sites(name, base_url, hostname, created_at, updated_at)
        VALUES('Fixture', 'https://example.test', 'example.test', 'now', 'now');
        INSERT INTO profile_fields(field, value_enc, updated_at)
        VALUES('full_name', X'0102', 'now');
        """
    )
    db.commit()
    db.close()


def test_control_migration_creates_onboarding_and_recipe_schema(tmp_path):
    from workflow.schema import migrate_control

    path = tmp_path / "control.db"
    legacy_control(path)

    assert migrate_control(path) == [1, 2, 3, 4, 5, 6, 7]
    assert migrate_control(path) == []

    db = sqlite3.connect(path)
    tables = {
        row[0]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {
        "candidate_profiles",
        "candidate_facts",
        "resume_versions",
        "resume_parse_facts",
        "preference_sets",
        "preference_rules",
        "job_preferences",
        "answer_entries",
        "answer_bank",
        "site_accounts",
        "site_manifests",
        "workflow_recipes",
        "operator_tasks",
        "review_issues",
        "readiness_checks",
        "browser_session_leases",
        "operator_decisions",
        "llm_providers",
    } <= tables
    assert db.execute("SELECT name FROM sites").fetchall() == [("Fixture",)]
    assert db.execute("SELECT field FROM profile_fields").fetchall() == [
        ("full_name",)
    ]
    assert db.execute(
        "SELECT version FROM schema_migrations WHERE database_name='control' ORDER BY version"
    ).fetchall() == [(1,), (2,), (3,), (4,), (5,), (6,), (7,)]
    send_columns = {
        row[1] for row in db.execute("PRAGMA table_info(cold_email_sends)")
    }
    assert {"approved_at", "claimed_by", "lease_expires_at", "attempt_count", "sent_at"} <= send_columns
    send_sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='cold_email_sends'"
    ).fetchone()[0]
    assert "unknown" in send_sql
    db.close()


def test_control_v2_adds_discovery_tables_idempotently(tmp_path):
    from workflow.schema import migrate_control

    path = tmp_path / "control.db"
    legacy_control(path)
    first = migrate_control(path)
    assert 2 in first
    assert migrate_control(path) == []

    db = sqlite3.connect(path)
    tables = {
        row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {
        "external_sources",
        "extracted_entities",
        "company_watchlist",
        "cold_email_templates",
        "cold_contacts",
        "cold_email_sends",
        "smtp_settings",
        "control_flags",
    } <= tables
    flags = dict(db.execute("SELECT key, value FROM control_flags"))
    assert flags.get("ingest_enabled") == "0"
    assert db.execute("SELECT name FROM sites").fetchall() == [("Fixture",)]
    indexes = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name IS NOT NULL"
        )
    }
    assert "idx_watchlist_url" in indexes
    db.close()


def test_control_v6_reconciles_legacy_duplicate_queued_sends(tmp_path):
    from workflow.schema import migrate_control

    control = tmp_path / "control.db"
    migrate_control(control)
    with sqlite3.connect(control) as db:
        db.execute("DROP INDEX idx_cold_email_one_active")
        db.execute(
            "DELETE FROM schema_migrations WHERE database_name='control' AND version=6"
        )
        db.execute(
            """INSERT INTO cold_contacts(
                 id,company,email,email_norm,status,created_at,updated_at
               ) VALUES('contact-dup','Acme','jobs@acme.test','jobs@acme.test',
                        'drafted','2026-01-01','2026-01-01')"""
        )
        for send_id, created in (("send-old", "2026-01-01"), ("send-new", "2026-01-02")):
            db.execute(
                """INSERT INTO cold_email_sends(
                     id,contact_id,status,created_at,updated_at
                   ) VALUES(?, 'contact-dup', 'queued', ?, ?)""",
                (send_id, created, created),
            )
    assert migrate_control(control) == [6]
    with sqlite3.connect(control) as db:
        rows = db.execute(
            "SELECT id,status,error FROM cold_email_sends WHERE contact_id='contact-dup' ORDER BY id"
        ).fetchall()
        assert rows == [
            ("send-new", "queued", None),
            ("send-old", "cancelled", "superseded_by_migration"),
        ]
