import os
import sqlite3
import sys


def test_worker_external_db_honors_jobhunt_queue_db(tmp_path, monkeypatch):
    queue = tmp_path / "prod_queue.db"
    with sqlite3.connect(queue) as db:
        db.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO jobs(id) VALUES ('ext-1')")
    monkeypatch.setenv("JOBHUNT_QUEUE_DB", str(queue))
    monkeypatch.setattr(sys, "argv", ["worker_external.py", "ext-test"])
    import importlib
    import worker_external

    importlib.reload(worker_external)
    with worker_external.db() as conn:
        names = [row[2] for row in conn.execute("PRAGMA database_list")]
        ids = [row[0] for row in conn.execute("SELECT id FROM jobs")]
    assert any(os.path.realpath(name) == os.path.realpath(queue) for name in names if name)
    assert ids == ["ext-1"]
