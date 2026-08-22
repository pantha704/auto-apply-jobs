from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

QUEUE_VERSION = 1
CONTROL_VERSION = 4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def _execute_script(db: sqlite3.Connection, script: str) -> None:
    """Execute simple migration DDL without sqlite3.executescript auto-commit."""
    for statement in script.split(";"):
        if statement.strip():
            db.execute(statement)


def migrate_queue(path: str | Path) -> list[int]:
    """Apply additive queue migrations and return versions applied this call."""
    db = sqlite3.connect(path, timeout=10)
    applied: list[int] = []
    try:
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
            database_name TEXT NOT NULL,
            version INTEGER NOT NULL,
            applied_at TEXT NOT NULL,
            PRIMARY KEY(database_name, version)
            )"""
        )
        seen = {
            row[0]
            for row in db.execute(
                "SELECT version FROM schema_migrations WHERE database_name='queue'"
            )
        }
        if QUEUE_VERSION not in seen:
            columns = _columns(db, "jobs")
            additions = {
                "claimed_at": "TEXT",
                "lease_expires_at": "TEXT",
                "attempt_count": "INTEGER NOT NULL DEFAULT 0",
                "next_attempt_at": "TEXT",
                "last_outcome_code": "TEXT",
                "normalized_payload": "TEXT",
                "preference_set_id": "TEXT",
                "preference_score": "REAL",
                "eligibility": "INTEGER CHECK(eligibility IS NULL OR eligibility IN (0,1))",
                "evaluation_code": "TEXT",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
            _execute_script(
                db,
                """
                CREATE INDEX idx_jobs_claimable_v2
                  ON jobs(status, eligibility, next_attempt_at, preference_score DESC, prio DESC);
                CREATE INDEX idx_jobs_expired_leases
                  ON jobs(status, lease_expires_at);

                CREATE TABLE application_runs (
                  id TEXT PRIMARY KEY,
                  job_id TEXT NOT NULL,
                  site_id INTEGER,
                  adapter TEXT NOT NULL,
                  recipe_id TEXT,
                  recipe_version INTEGER,
                  candidate_profile_id TEXT,
                  resume_version_id TEXT,
                  preference_set_id TEXT,
                  site_manifest_version INTEGER,
                  lease_token TEXT NOT NULL,
                  worker_id TEXT,
                  state TEXT NOT NULL,
                  started_at TEXT NOT NULL,
                  finished_at TEXT,
                  confirmed INTEGER NOT NULL DEFAULT 0 CHECK(confirmed IN (0,1)),
                  outcome_code TEXT,
                  safe_detail TEXT,
                  submission_evidence_json TEXT,
                  FOREIGN KEY(job_id) REFERENCES jobs(id)
                );
                CREATE INDEX idx_runs_job_time ON application_runs(job_id, started_at DESC);
                CREATE INDEX idx_runs_state_time ON application_runs(state, started_at DESC);

                CREATE TABLE job_attempts (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  run_id TEXT NOT NULL,
                  attempt_no INTEGER NOT NULL,
                  started_at TEXT NOT NULL,
                  finished_at TEXT,
                  outcome_code TEXT,
                  retryable INTEGER NOT NULL DEFAULT 0 CHECK(retryable IN (0,1)),
                  safe_detail TEXT,
                  UNIQUE(run_id, attempt_no),
                  FOREIGN KEY(run_id) REFERENCES application_runs(id)
                );
                CREATE INDEX idx_attempts_run ON job_attempts(run_id, attempt_no);

                CREATE TABLE worker_instances (
                  id TEXT PRIMARY KEY,
                  unit TEXT,
                  adapter TEXT,
                  state TEXT NOT NULL,
                  current_run_id TEXT,
                  heartbeat_at TEXT NOT NULL,
                  last_success_at TEXT,
                  browser_pid INTEGER,
                  queue_depth INTEGER NOT NULL DEFAULT 0,
                  safe_detail TEXT,
                  FOREIGN KEY(current_run_id) REFERENCES application_runs(id)
                );
                CREATE INDEX idx_workers_heartbeat ON worker_instances(heartbeat_at DESC);

                CREATE TABLE artifacts (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  run_id TEXT NOT NULL,
                  attempt_id INTEGER,
                  kind TEXT NOT NULL,
                  path TEXT NOT NULL,
                  storage_key TEXT,
                  sha256 TEXT NOT NULL,
                  size_bytes INTEGER,
                  pii_class TEXT NOT NULL,
                  redaction_status TEXT NOT NULL DEFAULT 'pending',
                  approved_for_sensitive_access INTEGER NOT NULL DEFAULT 0 CHECK(approved_for_sensitive_access IN (0,1)),
                  created_at TEXT NOT NULL,
                  retain_until TEXT,
                  FOREIGN KEY(run_id) REFERENCES application_runs(id),
                  FOREIGN KEY(attempt_id) REFERENCES job_attempts(id)
                );
                CREATE INDEX idx_artifacts_run ON artifacts(run_id, created_at);

                CREATE TABLE preference_evaluations (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  job_id TEXT NOT NULL,
                  preference_set_id TEXT NOT NULL,
                  eligible INTEGER NOT NULL CHECK(eligible IN (0,1)),
                  score REAL NOT NULL,
                  trace_json TEXT NOT NULL,
                  evaluated_at TEXT NOT NULL,
                  UNIQUE(job_id, preference_set_id),
                  FOREIGN KEY(job_id) REFERENCES jobs(id)
                );

                CREATE TABLE workflow_actions (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  run_id TEXT NOT NULL,
                  attempt_id INTEGER NOT NULL,
                  ordinal INTEGER NOT NULL,
                  action_type TEXT NOT NULL,
                  intent TEXT NOT NULL,
                  target_ref TEXT,
                  input_ref TEXT,
                  source TEXT NOT NULL CHECK(source IN ('recipe','operator','recovery')),
                  precondition_json TEXT,
                  postcondition_json TEXT,
                  status TEXT NOT NULL,
                  started_at TEXT NOT NULL,
                  finished_at TEXT,
                  safe_detail TEXT,
                  UNIQUE(attempt_id, ordinal),
                  FOREIGN KEY(run_id) REFERENCES application_runs(id),
                  FOREIGN KEY(attempt_id) REFERENCES job_attempts(id)
                );
                CREATE INDEX idx_actions_run ON workflow_actions(run_id, ordinal);

                CREATE TABLE recipes (
                  id TEXT NOT NULL,
                  version INTEGER NOT NULL,
                  adapter TEXT NOT NULL,
                  manifest_version INTEGER NOT NULL,
                  document_json TEXT NOT NULL,
                  sha256 TEXT NOT NULL,
                  status TEXT NOT NULL CHECK(status IN ('draft','verified','retired')),
                  verified_at TEXT,
                  PRIMARY KEY(id, version)
                );

                CREATE TABLE metric_rollups (
                  bucket_start TEXT NOT NULL,
                  bucket_seconds INTEGER NOT NULL,
                  worker_id TEXT NOT NULL DEFAULT '',
                  adapter TEXT NOT NULL DEFAULT '',
                  outcome_code TEXT NOT NULL DEFAULT '',
                  count INTEGER NOT NULL DEFAULT 0,
                  duration_ms INTEGER NOT NULL DEFAULT 0,
                  PRIMARY KEY(bucket_start, bucket_seconds, worker_id, adapter, outcome_code)
                );
                """
            )
            db.execute(
                "INSERT INTO schema_migrations(database_name,version,applied_at) VALUES('queue',?,?)",
                (QUEUE_VERSION, _now()),
            )
            applied.append(QUEUE_VERSION)
        db.commit()
        return applied
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def migrate_control(path: str | Path) -> list[int]:
    """Apply additive control-plane migrations and return applied versions."""
    db = sqlite3.connect(path, timeout=10)
    applied: list[int] = []
    try:
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
            database_name TEXT NOT NULL,
            version INTEGER NOT NULL,
            applied_at TEXT NOT NULL,
            PRIMARY KEY(database_name, version)
            )"""
        )
        seen = {
            row[0]
            for row in db.execute(
                "SELECT version FROM schema_migrations WHERE database_name='control'"
            )
        }
        if 1 not in seen:
            _execute_script(
                db,
                """
                CREATE TABLE candidate_profiles (
                  id TEXT PRIMARY KEY,
                  revision INTEGER NOT NULL UNIQUE,
                  status TEXT NOT NULL CHECK(status IN ('draft','approved','superseded')),
                  created_at TEXT NOT NULL,
                  approved_at TEXT,
                  source_resume_version_id TEXT
                );

                CREATE TABLE candidate_facts (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  profile_id TEXT NOT NULL,
                  namespace TEXT NOT NULL,
                  field TEXT NOT NULL,
                  value_enc BLOB NOT NULL,
                  value_type TEXT NOT NULL,
                  source TEXT NOT NULL CHECK(source IN ('user','resume_parser','legacy_import')),
                  source_ref TEXT,
                  confidence REAL,
                  user_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(user_confirmed IN (0,1)),
                  UNIQUE(profile_id, namespace, field),
                  FOREIGN KEY(profile_id) REFERENCES candidate_profiles(id)
                );
                CREATE INDEX idx_candidate_facts_active
                  ON candidate_facts(profile_id, namespace, field);

                CREATE TABLE resume_versions (
                  id TEXT PRIMARY KEY,
                  sha256 TEXT NOT NULL UNIQUE,
                  original_name TEXT NOT NULL,
                  media_type TEXT NOT NULL,
                  size_bytes INTEGER NOT NULL,
                  storage_key TEXT NOT NULL UNIQUE,
                  parser_name TEXT,
                  parser_version TEXT,
                  parse_status TEXT NOT NULL CHECK(parse_status IN ('pending','parsed','failed','approved','rejected')),
                  extracted_text_sha256 TEXT,
                  created_at TEXT NOT NULL,
                  parsed_at TEXT,
                  approved_at TEXT,
                  supersedes_id TEXT,
                  safe_error TEXT,
                  FOREIGN KEY(supersedes_id) REFERENCES resume_versions(id)
                );
                CREATE INDEX idx_resume_active ON resume_versions(parse_status, created_at DESC);

                CREATE TABLE resume_parse_facts (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  resume_version_id TEXT NOT NULL,
                  namespace TEXT NOT NULL,
                  field TEXT NOT NULL,
                  proposed_value_enc BLOB NOT NULL,
                  source_page INTEGER,
                  source_start INTEGER,
                  source_end INTEGER,
                  confidence REAL,
                  user_action TEXT NOT NULL DEFAULT 'pending' CHECK(user_action IN ('pending','accepted','edited','rejected')),
                  approved_profile_id TEXT,
                  FOREIGN KEY(resume_version_id) REFERENCES resume_versions(id),
                  FOREIGN KEY(approved_profile_id) REFERENCES candidate_profiles(id)
                );

                CREATE TABLE job_preferences (
                  preference_key TEXT NOT NULL,
                  version INTEGER NOT NULL,
                  value_enc BLOB NOT NULL,
                  strength TEXT NOT NULL CHECK(strength IN ('hard','soft','none')),
                  approved INTEGER NOT NULL DEFAULT 0 CHECK(approved IN (0,1)),
                  created_at TEXT NOT NULL,
                  approved_at TEXT,
                  PRIMARY KEY(preference_key, version)
                );

                CREATE TABLE preference_sets (
                  id TEXT PRIMARY KEY,
                  version INTEGER NOT NULL UNIQUE,
                  status TEXT NOT NULL CHECK(status IN ('draft','active','superseded')),
                  created_at TEXT NOT NULL,
                  activated_at TEXT
                );
                CREATE TABLE preference_rules (
                  id TEXT PRIMARY KEY,
                  preference_set_id TEXT NOT NULL,
                  criterion TEXT NOT NULL,
                  mode TEXT NOT NULL CHECK(mode IN ('hard','soft','none')),
                  operator TEXT NOT NULL,
                  expected_json TEXT NOT NULL,
                  weight REAL NOT NULL CHECK(weight >= 0),
                  unknown_policy TEXT NOT NULL DEFAULT 'block' CHECK(unknown_policy IN ('block','review','ignore')),
                  ordinal INTEGER NOT NULL,
                  UNIQUE(preference_set_id, criterion),
                  FOREIGN KEY(preference_set_id) REFERENCES preference_sets(id)
                );

                CREATE TABLE answer_entries (
                  id TEXT PRIMARY KEY,
                  question_key TEXT NOT NULL,
                  answer_enc BLOB NOT NULL,
                  answer_type TEXT NOT NULL,
                  scope_json TEXT NOT NULL,
                  version INTEGER NOT NULL,
                  status TEXT NOT NULL CHECK(status IN ('draft','approved','retired')),
                  provenance TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  approved_at TEXT,
                  UNIQUE(question_key, version)
                );

                CREATE TABLE answer_bank (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  question_key TEXT NOT NULL,
                  version INTEGER NOT NULL,
                  scope TEXT NOT NULL,
                  answer_enc BLOB NOT NULL,
                  approved INTEGER NOT NULL DEFAULT 0 CHECK(approved IN (0,1)),
                  created_at TEXT NOT NULL,
                  approved_at TEXT,
                  UNIQUE(question_key, scope, version)
                );
                CREATE INDEX idx_answer_lookup
                  ON answer_bank(question_key, scope, approved, version DESC);

                CREATE TABLE site_accounts (
                  id TEXT PRIMARY KEY,
                  site_id INTEGER NOT NULL,
                  account_label TEXT NOT NULL,
                  auth_type TEXT NOT NULL,
                  session_ref TEXT,
                  enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  FOREIGN KEY(site_id) REFERENCES sites(id)
                );

                CREATE TABLE site_manifests (
                  id TEXT PRIMARY KEY,
                  site_id INTEGER NOT NULL,
                  hostname_pattern TEXT NOT NULL,
                  adapter TEXT NOT NULL,
                  version INTEGER NOT NULL,
                  manifest_json TEXT NOT NULL,
                  active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0,1)),
                  verified_at TEXT,
                  created_at TEXT NOT NULL,
                  UNIQUE(site_id, version),
                  FOREIGN KEY(site_id) REFERENCES sites(id)
                );

                CREATE TABLE workflow_recipes (
                  id TEXT NOT NULL,
                  version INTEGER NOT NULL,
                  manifest_id TEXT NOT NULL,
                  name TEXT NOT NULL,
                  recipe_json TEXT NOT NULL,
                  postcondition_json TEXT NOT NULL,
                  active INTEGER NOT NULL DEFAULT 0 CHECK(active IN (0,1)),
                  verified_at TEXT,
                  created_at TEXT NOT NULL,
                  PRIMARY KEY(id, version),
                  FOREIGN KEY(manifest_id) REFERENCES site_manifests(id)
                );
                CREATE INDEX idx_recipe_manifest
                  ON workflow_recipes(manifest_id, active, version DESC);

                CREATE TABLE operator_tasks (
                  id TEXT PRIMARY KEY,
                  run_id TEXT,
                  site_id INTEGER,
                  type TEXT NOT NULL CHECK(type IN ('unknown_question','login_required','recipe_drift','ambiguous_confirmation','manual_review')),
                  status TEXT NOT NULL CHECK(status IN ('open','resolved','dismissed')),
                  safe_summary TEXT NOT NULL,
                  artifact_id INTEGER,
                  created_at TEXT NOT NULL,
                  resolved_at TEXT,
                  FOREIGN KEY(site_id) REFERENCES sites(id)
                );
                CREATE INDEX idx_operator_tasks_status
                  ON operator_tasks(status, type, created_at DESC);

                CREATE TABLE review_issues (
                  id TEXT PRIMARY KEY,
                  run_id TEXT,
                  site_id INTEGER,
                  kind TEXT NOT NULL,
                  severity TEXT NOT NULL,
                  status TEXT NOT NULL,
                  safe_summary TEXT NOT NULL,
                  payload_enc BLOB,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  resolved_at TEXT,
                  FOREIGN KEY(site_id) REFERENCES sites(id)
                );
                CREATE INDEX idx_review_issues_status
                  ON review_issues(status, severity, created_at DESC);

                CREATE TABLE readiness_checks (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  code TEXT NOT NULL,
                  scope TEXT NOT NULL,
                  subject TEXT NOT NULL,
                  status TEXT NOT NULL,
                  safe_detail TEXT,
                  checked_at TEXT NOT NULL,
                  expires_at TEXT,
                  UNIQUE(code, scope, subject)
                );
                CREATE INDEX idx_readiness_expiry ON readiness_checks(expires_at);

                CREATE TABLE browser_session_leases (
                  session_ref TEXT PRIMARY KEY,
                  owner_id TEXT NOT NULL,
                  run_id TEXT,
                  acquired_at TEXT NOT NULL,
                  heartbeat_at TEXT NOT NULL,
                  expires_at TEXT NOT NULL
                );
                CREATE INDEX idx_browser_lease_expiry
                  ON browser_session_leases(expires_at);

                CREATE TABLE llm_providers (
                  id TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  base_url TEXT NOT NULL,
                  model TEXT NOT NULL,
                  api_key_enc BLOB NOT NULL,
                  enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
                  recovery_only INTEGER NOT NULL DEFAULT 1 CHECK(recovery_only = 1),
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );

                CREATE TABLE operator_decisions (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  issue_id TEXT,
                  task_id TEXT,
                  decision_type TEXT NOT NULL,
                  safe_summary TEXT NOT NULL,
                  payload_enc BLOB,
                  approved_ref_type TEXT,
                  approved_ref_id TEXT,
                  actor TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(issue_id) REFERENCES review_issues(id),
                  FOREIGN KEY(task_id) REFERENCES operator_tasks(id)
                );
                CREATE INDEX idx_operator_decisions_issue
                  ON operator_decisions(issue_id, created_at);
                """
            )
            db.execute(
                "INSERT INTO schema_migrations(database_name,version,applied_at) VALUES('control',?,?)",
                (1, _now()),
            )
            applied.append(1)
        if 2 not in seen:
            _execute_script(
                db,
                """
                CREATE TABLE external_sources (
                  id TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  url TEXT NOT NULL UNIQUE,
                  kind TEXT NOT NULL CHECK(kind IN ('sheet','csv','html','json','manual')),
                  category TEXT,
                  status TEXT NOT NULL CHECK(status IN ('queued','ingesting','ready','error','paused')) DEFAULT 'queued',
                  owner TEXT NOT NULL CHECK(owner IN ('n8n','api')) DEFAULT 'n8n',
                  lease_until TEXT,
                  error_count INTEGER NOT NULL DEFAULT 0,
                  parent_id TEXT,
                  gid TEXT,
                  tabs_json TEXT,
                  last_ingested_at TEXT,
                  last_error TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE extracted_entities (
                  id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL REFERENCES external_sources(id),
                  company TEXT,
                  website TEXT,
                  careers_url TEXT,
                  apply_url TEXT,
                  email TEXT,
                  role TEXT,
                  location TEXT,
                  requirements TEXT,
                  entity_kind TEXT NOT NULL CHECK(entity_kind IN ('company','job','email','unknown')),
                  canonical_key TEXT NOT NULL,
                  raw_json TEXT,
                  routed TEXT CHECK(routed IN ('apply','watchlist','cold_email','review','dropped')) DEFAULT 'review',
                  routed_at TEXT,
                  created_at TEXT NOT NULL,
                  UNIQUE(source_id, canonical_key)
                );
                CREATE TABLE company_watchlist (
                  id TEXT PRIMARY KEY,
                  company TEXT,
                  website TEXT,
                  careers_url TEXT NOT NULL,
                  source_id TEXT,
                  status TEXT NOT NULL CHECK(status IN ('pending','crawled','no_jobs','error')) DEFAULT 'pending',
                  last_crawled_at TEXT,
                  created_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX idx_watchlist_url
                  ON company_watchlist(careers_url)
                  WHERE length(trim(careers_url)) > 8;
                CREATE TABLE cold_email_templates (
                  id TEXT PRIMARY KEY,
                  name TEXT NOT NULL,
                  subject TEXT NOT NULL,
                  body TEXT NOT NULL,
                  is_default INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE cold_contacts (
                  id TEXT PRIMARY KEY,
                  company TEXT,
                  email TEXT NOT NULL,
                  email_norm TEXT NOT NULL UNIQUE,
                  website TEXT,
                  role TEXT,
                  requirements TEXT,
                  source_id TEXT,
                  extracted_id TEXT,
                  template_id TEXT,
                  status TEXT NOT NULL CHECK(status IN (
                    'queued','drafted','sent','failed','do_not_contact'
                  )) DEFAULT 'queued',
                  last_sent_at TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT
                );
                CREATE INDEX idx_cold_contacts_latest ON cold_contacts(COALESCE(updated_at, created_at) DESC);
                CREATE TABLE cold_email_sends (
                  id TEXT PRIMARY KEY,
                  contact_id TEXT NOT NULL REFERENCES cold_contacts(id),
                  template_id TEXT,
                  subject TEXT,
                  status TEXT NOT NULL CHECK(status IN ('queued','sent','failed','cancelled')),
                  provider_id TEXT,
                  error TEXT,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE smtp_settings (
                  id INTEGER PRIMARY KEY CHECK(id=1),
                  host TEXT,
                  port INTEGER,
                  username_enc BLOB,
                  password_enc BLOB,
                  from_address TEXT,
                  enabled INTEGER NOT NULL DEFAULT 0,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE control_flags (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                );
                INSERT INTO control_flags(key, value) VALUES('ingest_enabled', '0');
                """
            )
            db.execute(
                "INSERT INTO schema_migrations(database_name,version,applied_at) VALUES('control',?,?)",
                (2, _now()),
            )
            applied.append(2)
        if 3 not in seen:
            columns = _columns(db, "cold_contacts")
            additions = {
                "draft_subject": "TEXT",
                "draft_body": "TEXT",
                "drafted_at": "TEXT",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE cold_contacts ADD COLUMN {name} {declaration}")
            db.execute(
                "INSERT INTO schema_migrations(database_name,version,applied_at) VALUES('control',?,?)",
                (3, _now()),
            )
            applied.append(3)
        if 4 not in seen:
            db.execute(
                """INSERT OR IGNORE INTO cold_email_templates(
                     id,name,subject,body,is_default,created_at,updated_at
                   ) VALUES(
                     'default-manual-outreach',
                     'Manual company outreach',
                     'Interest in {{role}} at {{company}}',
                     'Hello {{company}} Hiring Team,\n\nI am reaching out regarding {{role}} opportunities at {{company}}. My background aligns with the role, and I would welcome the chance to discuss how I could contribute.\n\nI have attached my resume for review.\n\nBest regards,',
                     1,datetime('now'),datetime('now')
                   )"""
            )
            db.execute(
                "INSERT INTO schema_migrations(database_name,version,applied_at) VALUES('control',?,?)",
                (4, _now()),
            )
            applied.append(4)
        db.commit()
        return applied
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
