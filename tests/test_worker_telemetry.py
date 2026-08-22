import json
import sqlite3

from workflow.schema import migrate_queue
from workflow.worker_telemetry import WorkerTelemetry


def _queue(path):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY,status TEXT,portal TEXT,url TEXT,title TEXT,prio INTEGER)")
    migrate_queue(path)


def test_worker_telemetry_persists_runtime_events_and_private_projection(tmp_path):
    db = tmp_path / "queue.db"
    _queue(db)
    telemetry = WorkerTelemetry(db, tmp_path / "state", "wf-w1", "wellfound")
    telemetry.started()
    telemetry.claimed("job-1", queue_depth=4)
    telemetry.outcome("job-1", "done", "applied|person@example.test https://secret.test role title")
    telemetry.idle(queue_depth=3)

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        runtime = dict(conn.execute("SELECT * FROM worker_instances WHERE id='wf-w1'").fetchone())
        events = [dict(row) for row in conn.execute(
            "SELECT event,job_id,outcome_code,safe_detail FROM worker_events ORDER BY id"
        )]
    assert runtime["state"] == "idle"
    assert runtime["queue_depth"] == 3
    assert runtime["current_job_id"] is None
    assert runtime["last_success_at"]
    assert [event["event"] for event in events] == ["started", "claimed", "outcome", "idle"]
    assert events[2]["outcome_code"] == "done"

    root = tmp_path / "state" / "wellfound" / "wf-w1"
    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / "status.json").stat().st_mode & 0o777 == 0o600
    assert (root / "events.jsonl").stat().st_mode & 0o777 == 0o600
    status = json.loads((root / "status.json").read_text())
    assert status["state"] == "idle"
    text = (root / "events.jsonl").read_text()
    assert "https://" not in text
    assert "@" not in text
    assert "role title" not in text
    assert events[2]["safe_detail"] == "applied"


def test_stale_heartbeat_projection_cannot_overwrite_newer_outcome(tmp_path):
    db = tmp_path / "queue.db"
    _queue(db)
    root = tmp_path / "state"
    telemetry = WorkerTelemetry(db, root, "wf-w1", "wellfound")
    telemetry.claimed("job-1", queue_depth=1)
    telemetry.outcome("job-1", "done", queue_depth=0)

    telemetry._project(
        {},
        {
            "worker_id": "wf-w1",
            "adapter": "wellfound",
            "state": "working",
            "current_job_id": "job-1",
            "queue_depth": 1,
            "safe_detail": "",
            "updated_at": "stale-heartbeat",
        },
        emit_event=False,
    )

    status = json.loads((root / "wellfound" / "wf-w1" / "status.json").read_text())
    assert status["state"] == "idle"
    assert status["current_job_id"] is None
    assert status["queue_depth"] == 0


def test_queue_migration_adds_worker_history_idempotently(tmp_path):
    db = tmp_path / "queue.db"
    _queue(db)
    assert migrate_queue(db) == []
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='worker_events'"
        ).fetchone()
        columns = {row[1] for row in conn.execute("PRAGMA table_info(worker_instances)")}
    assert {"current_job_id", "started_at", "last_event_at"} <= columns


def test_worker_queue_depth_is_read_from_canonical_jobs_table(tmp_path):
    db = tmp_path / "queue.db"
    _queue(db)
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO jobs(id,status,portal,url,title,prio) VALUES(?,?,?,?,?,?)",
            [("a", "pending", "wellfound", "", "", 1),
             ("b", "pending", "wellfound", "", "", 1),
             ("c", "done", "wellfound", "", "", 1)],
        )
    telemetry = WorkerTelemetry(db, tmp_path / "state", "wf-w1", "wellfound")
    telemetry.idle()
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT queue_depth FROM worker_instances WHERE id='wf-w1'"
        ).fetchone()[0] == 2
