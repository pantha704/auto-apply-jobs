from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from workflow.analytics import aggregate_analytics


NOW = datetime(2026, 8, 19, 12, tzinfo=timezone.utc)


def _db(path, *, v2=True):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE jobs(id TEXT PRIMARY KEY, portal TEXT, status TEXT, fetched_at TEXT, lease_expires_at TEXT, attempt_count INTEGER)")
    db.execute("CREATE TABLE applications(id INTEGER PRIMARY KEY, portal TEXT, company TEXT, role TEXT, url TEXT, applied_at TEXT, status TEXT)")
    if v2:
        db.execute("CREATE TABLE application_runs(id TEXT PRIMARY KEY, job_id TEXT, state TEXT, started_at TEXT, finished_at TEXT, confirmed INTEGER, outcome_code TEXT)")
        db.execute("CREATE TABLE job_attempts(id INTEGER PRIMARY KEY, run_id TEXT, attempt_no INTEGER, started_at TEXT, finished_at TEXT, outcome_code TEXT, retryable INTEGER)")
        db.execute("CREATE TABLE worker_instances(id TEXT PRIMARY KEY, state TEXT, heartbeat_at TEXT, queue_depth INTEGER)")
    db.commit()
    return db


def test_live_v2_aggregation_is_metadata_only(tmp_path):
    path = tmp_path / "queue.db"
    db = _db(path)
    db.executemany("INSERT INTO jobs VALUES(?,?,?,?,?,?)", [
        ("j1", "greenhouse", "done", "2026-08-19T08:00:00+00:00", None, 1),
        ("j2", "lever", "claimed", "2026-08-19T09:00:00+00:00", "2026-08-19T11:00:00+00:00", 2),
        ("j3", "lever", "pending", "2026-08-19T10:00:00+00:00", None, 0),
    ])
    db.executemany("INSERT INTO application_runs VALUES(?,?,?,?,?,?,?)", [
        ("r1", "j1", "submitted", "2026-08-19T08:00:00+00:00", "2026-08-19T08:02:00+00:00", 1, "submitted"),
        ("r2", "j2", "finished", "2026-08-19T09:00:00+00:00", "2026-08-19T09:01:00+00:00", 0, "ui_drift"),
    ])
    db.executemany("INSERT INTO job_attempts VALUES(?,?,?,?,?,?,?)", [
        (1, "r1", 1, "2026-08-19T08:00:00+00:00", "2026-08-19T08:02:00+00:00", "submitted", 0),
        (2, "r2", 1, "2026-08-19T09:00:00+00:00", "2026-08-19T09:01:00+00:00", "ui_drift", 1),
    ])
    db.execute("INSERT INTO worker_instances VALUES('w1','idle','2026-08-19T11:59:00+00:00',3)")
    db.commit(); db.close()

    result = aggregate_analytics(path, "24h", now=NOW)

    assert result["range"] == {"name": "24h", "start": "2026-08-18T12:00:00+00:00", "end": NOW.isoformat(), "bucket": "hourly"}
    assert result["confirmed_applications"] == 1
    assert sum(point["count"] for point in result["timeline"]) == 1
    assert result["attempts"] == 2
    assert result["outcomes"] == {"submitted": 1, "ui_drift": 1}
    assert result["portals"] == {"greenhouse": 1, "lever": 1}
    assert result["queue_depth"] == {"pending": 1, "claimed": 1, "total": 2, "worker_reported": 3}
    assert result["success_rate"] == 0.5
    assert result["durations_ms"] == {"count": 2, "average": 90000, "minimum": 60000, "maximum": 120000}
    assert result["leases"] == {"expired": 1, "recoveries": 1}
    assert result["workers"] == {"total": 1, "by_state": {"idle": 1}}
    assert "company" not in repr(result).lower()


def test_legacy_database_has_honest_capabilities_and_empty_v2_metrics(tmp_path):
    path = tmp_path / "queue.db"
    db = _db(path, v2=False)
    db.execute("INSERT INTO applications VALUES(1,'indeed','Secret Co','Role','https://private','2026-08-18T10:00:00+00:00','submitted')")
    db.execute("INSERT INTO jobs VALUES('j1','indeed','pending','2026-08-18T09:00:00+00:00',NULL,0)")
    db.commit(); db.close()

    result = aggregate_analytics(path, "7d", now=NOW)

    assert result["capabilities"] == {"applications": True, "application_runs": False, "job_attempts": False, "jobs": True, "worker_instances": False}
    assert result["confirmed_applications"] == 1
    assert result["attempts"] == 0
    assert result["outcomes"] == {}
    assert result["portals"] == {"indeed": 1}
    assert result["durations_ms"] == {"count": 0, "average": None, "minimum": None, "maximum": None}
    assert "secret" not in repr(result).lower()
    assert "private" not in repr(result).lower()


def test_custom_range_and_bucket_validation(tmp_path):
    import pytest
    path = tmp_path / "queue.db"
    _db(path).close()
    result = aggregate_analytics(path, "custom", now=NOW, custom_start="2026-01-01T00:00:00Z", custom_end="2026-08-01T00:00:00Z")
    assert result["range"]["bucket"] == "monthly"
    with pytest.raises(ValueError):
        aggregate_analytics(path, "bogus", now=NOW)
    with pytest.raises(ValueError):
        aggregate_analytics(path, "24h", now=NOW, bucket="yearly")
