from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from job_identity import canonical_url, stable_job_id
from add_fresh_jobs import linkedin_location_eligible


ROOT = Path(__file__).resolve().parents[1]


def create_queue(database: Path, with_applications: bool = False) -> None:
    with sqlite3.connect(database) as db:
        db.execute(
            """CREATE TABLE jobs (
            id TEXT PRIMARY KEY, portal TEXT, url TEXT, title TEXT, source TEXT,
            status TEXT, claimed_by TEXT, result TEXT, prio INTEGER,
            posted_at TEXT, fetched_at TEXT
            )"""
        )
        if with_applications:
            db.execute("CREATE TABLE applications (url TEXT)")


def test_fresh_flag_cannot_recycle_explicitly_stale_linkedin_jobs(tmp_path: Path):
    database = tmp_path / "queue.db"
    create_queue(database)

    now = datetime.now(timezone.utc)
    harvest = tmp_path / "harvest.json"
    harvest.write_text(
        json.dumps(
            [
                {
                    "title": "Software Engineer",
                    "link": "https://example.test/stale",
                    "source": "linkedin",
                    "location": "Remote",
                    "scopes": ["india_remote"],
                    "date": (now - timedelta(days=3)).isoformat(),
                },
                {
                    "title": "Software Engineer",
                    "link": "https://example.test/fresh",
                    "source": "linkedin",
                    "location": "Remote",
                    "scopes": ["india_remote"],
                    "date": (now - timedelta(hours=1)).isoformat(),
                },
            ]
        ),
        encoding="utf-8",
    )
    env = {**os.environ, "JOBHUNT_QUEUE_DB": str(database)}
    result = subprocess.run(
        [sys.executable, str(ROOT / "add_fresh_jobs.py"), str(harvest),
         "--portal", "linkedin", "--fresh"],
        cwd=ROOT, env=env, text=True, capture_output=True, check=True,
    )

    with sqlite3.connect(database) as db:
        urls = [row[0] for row in db.execute("SELECT url FROM jobs")]
    assert urls == ["https://example.test/fresh"]
    assert "added 1 new jobs" in result.stdout
    assert "missing: linkedin" not in result.stdout


def test_durable_application_and_tracking_variant_are_not_reinjected(tmp_path: Path):
    database = tmp_path / "queue.db"
    create_queue(database, with_applications=True)
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO applications(url) VALUES (?)",
            ("https://www.linkedin.com/jobs/view/123?trk=old",),
        )

    now = datetime.now(timezone.utc).isoformat()
    harvest = tmp_path / "harvest.json"
    harvest.write_text(json.dumps([
        {"title": "Software Engineer", "link": "https://www.linkedin.com/jobs/view/123?trackingId=new", "source": "linkedin", "location": "Remote", "scopes": ["india_remote"], "date": now},
        {"title": "Software Engineer", "link": "https://www.linkedin.com/jobs/view/124?trackingId=new", "source": "linkedin", "location": "Remote", "scopes": ["india_remote"], "date": now},
    ]))
    result = subprocess.run(
        [sys.executable, str(ROOT / "add_fresh_jobs.py"), str(harvest), "--portal", "linkedin", "--fresh"],
        cwd=ROOT,
        env={**os.environ, "JOBHUNT_QUEUE_DB": str(database)},
        text=True,
        capture_output=True,
        check=True,
    )
    with sqlite3.connect(database) as db:
        rows = db.execute("SELECT id,url FROM jobs").fetchall()
    assert rows == [(stable_job_id("linkedin", "https://www.linkedin.com/jobs/view/124?trackingId=new"), "https://www.linkedin.com/jobs/view/124?trackingId=new")]
    assert "added 1 new jobs" in result.stdout


def test_canonical_identity_is_stable_and_removes_tracking():
    first = "https://www.linkedin.com/jobs/view/123/?trackingId=one&utm_source=x#fragment"
    second = "https://www.linkedin.com/jobs/view/123?trk=two"
    assert canonical_url(first) == canonical_url(second)
    assert stable_job_id("linkedin", first) == stable_job_id("linkedin", second)


def test_linkedin_location_gate_rejects_country_locked_remote():
    assert linkedin_location_eligible("Bengaluru, Karnataka, India", ["india"])
    assert linkedin_location_eligible("Remote - Worldwide", ["remote"])
    assert linkedin_location_eligible("Greater Kolkata Area", [])
    assert not linkedin_location_eligible("New York, United States", ["remote"])
    assert not linkedin_location_eligible("San Francisco, CA", ["remote"])
    assert not linkedin_location_eligible("", ["remote"])


def test_site_injector_honors_configured_db_and_durable_applications(tmp_path: Path):
    database = tmp_path / "queue.db"
    create_queue(database, with_applications=True)
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO applications(url) VALUES ('https://example.test/applied?utm_source=old')")
    source = tmp_path / "sites.json"
    source.write_text(json.dumps([{"site": "internshala", "jobs": [
        {"title": "Full Stack Development", "link": "https://example.test/applied?utm_source=new"},
        {"title": "Java Development", "link": "https://example.test/new?utm_source=new"},
    ]}]))
    result = subprocess.run(
        [sys.executable, str(ROOT / "inject_site.py"), str(source)],
        cwd=ROOT,
        env={**os.environ, "JOBHUNT_QUEUE_DB": str(database)},
        text=True,
        capture_output=True,
        check=True,
    )
    with sqlite3.connect(database) as db:
        urls = [row[0] for row in db.execute("SELECT url FROM jobs")]
    assert urls == ["https://example.test/new?utm_source=new"]
    assert "added 1 jobs" in result.stdout


def test_site_injector_preserves_wellfound_time_seen_state_and_external_routing(tmp_path: Path):
    database = tmp_path / "queue.db"
    seen_db = tmp_path / "seen.db"
    create_queue(database, with_applications=True)
    source = tmp_path / "sites.json"
    source.write_text(json.dumps([
        {"site": "wellfound_fresh", "jobs": [{
            "title": "Junior Full Stack Engineer",
            "link": "https://wellfound.com/jobs/123-junior-full-stack-engineer",
            "posted_at": 1787000000,
        }]},
        {"site": "weworkremotely", "jobs": [{
            "title": "React Developer",
            "link": "https://weworkremotely.com/remote-jobs/react-developer",
        }]},
    ]))
    subprocess.run(
        [sys.executable, str(ROOT / "inject_site.py"), str(source)],
        cwd=ROOT,
        env={**os.environ, "JOBHUNT_QUEUE_DB": str(database), "WELLFOUND_SEEN_DB": str(seen_db)},
        text=True,
        capture_output=True,
        check=True,
    )
    with sqlite3.connect(database) as db:
        rows = db.execute("SELECT portal,source,posted_at,fetched_at FROM jobs ORDER BY portal").fetchall()
    assert rows[0][0:2] == ("external", "weworkremotely")
    assert rows[1][0:2] == ("wellfound", "wellfound_fresh")
    assert rows[1][2] and rows[1][3]
    with sqlite3.connect(seen_db) as db:
        assert db.execute("SELECT COUNT(*) FROM seen").fetchone()[0] == 1
