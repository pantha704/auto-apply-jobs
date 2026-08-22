from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

from workflow.cold_email import ColdEmailQueue, EventJournal, ColdEmailSender
from workflow.gmail_provider import GmailApiProvider


def _schema(path):
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE cold_contacts (
              id TEXT PRIMARY KEY, company TEXT, email TEXT, template_id TEXT,
              status TEXT, draft_subject TEXT, draft_body TEXT, updated_at TEXT,
              last_sent_at TEXT
            );
            CREATE TABLE cold_email_sends (
              id TEXT PRIMARY KEY, contact_id TEXT, template_id TEXT, subject TEXT,
              status TEXT, provider_id TEXT, error TEXT, created_at TEXT,
              body TEXT, approved_at TEXT, approved_by TEXT, claimed_by TEXT,
              claimed_at TEXT, lease_expires_at TEXT, attempt_count INTEGER DEFAULT 0,
              next_attempt_at TEXT, sent_at TEXT, updated_at TEXT
            );
            INSERT INTO cold_contacts VALUES(
              'c1','Acme','jobs@acme.test','t1','drafted','Engineer at Acme',
              'Hello Acme','2026-08-22T00:00:00+00:00',NULL
            );
            """
        )


def test_only_drafted_contact_can_be_approved_once(tmp_path):
    db = tmp_path / "control.db"
    _schema(db)
    queue = ColdEmailQueue(db)
    send_id = queue.approve("c1", approved_by="operator")
    assert queue.approve("c1", approved_by="operator") == send_id
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT status,approved_by,body FROM cold_email_sends").fetchone()
    assert row == ("queued", "operator", "Hello Acme")


def test_claim_is_atomic_and_marks_sent_with_provider_id(tmp_path):
    db = tmp_path / "control.db"
    _schema(db)
    queue = ColdEmailQueue(db)
    send_id = queue.approve("c1", approved_by="operator")
    claim = queue.claim("email-w1", lease_seconds=60)
    assert claim.id == send_id
    assert queue.claim("email-w2") is None
    queue.sent(claim, provider_id="gmail-message-1")
    with sqlite3.connect(db) as conn:
        send = conn.execute("SELECT status,provider_id FROM cold_email_sends").fetchone()
        contact = conn.execute("SELECT status FROM cold_contacts WHERE id='c1'").fetchone()[0]
    assert send == ("sent", "gmail-message-1")
    assert contact == "sent"


def test_retryable_failure_returns_to_queue_with_backoff(tmp_path):
    db = tmp_path / "control.db"
    _schema(db)
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    queue = ColdEmailQueue(db, now=lambda: now)
    queue.approve("c1", approved_by="operator")
    claim = queue.claim("email-w1")
    queue.failed(claim, "rate_limited", retryable=True, retry_after=timedelta(minutes=5))
    assert queue.claim("email-w1") is None
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT status,next_attempt_at,error FROM cold_email_sends").fetchone()
    assert row[0] == "queued"
    assert row[1].startswith("2026-08-22T00:05:00")
    assert row[2] == "rate_limited"


def test_expired_inflight_send_becomes_unknown_not_retried(tmp_path):
    db = tmp_path / "control.db"
    _schema(db)
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    clock = {"now": now}
    queue = ColdEmailQueue(db, now=lambda: clock["now"])
    queue.approve("c1", approved_by="operator")
    assert queue.claim("email-w1", lease_seconds=10) is not None
    clock["now"] = now + timedelta(seconds=11)
    assert queue.claim("email-w2") is None
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT status,error FROM cold_email_sends").fetchone()
    assert row == ("unknown", "delivery_confirmation_unknown")


def test_jsonl_journal_is_privacy_safe_and_append_only(tmp_path):
    journal = EventJournal(tmp_path / "email-w1" / "events.jsonl")
    journal.append("claimed", send_id="s1", contact_id="c1")
    journal.append("sent", send_id="s1", contact_id="c1", provider_id="gmail-1")
    rows = [json.loads(line) for line in journal.path.read_text().splitlines()]
    assert [row["event"] for row in rows] == ["claimed", "sent"]
    assert journal.path.stat().st_mode & 0o777 == 0o600
    assert "email" not in journal.path.read_text().lower()
    assert "body" not in journal.path.read_text().lower()


class FakeProvider:
    def __init__(self, ready=True, result="gmail-123"):
        self.ready = ready
        self.result = result
        self.sent = []

    def is_ready(self):
        return self.ready

    def send(self, *, to, subject, body):
        self.sent.append((to, subject, body))
        return self.result


def test_sender_does_not_claim_when_provider_is_not_authenticated(tmp_path):
    db = tmp_path / "control.db"
    _schema(db)
    queue = ColdEmailQueue(db)
    queue.approve("c1", approved_by="operator")
    sender = ColdEmailSender(queue, FakeProvider(ready=False), EventJournal(tmp_path / "events.jsonl"), "email-w1")
    assert sender.run_once() == "provider_not_authenticated"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT status FROM cold_email_sends").fetchone()[0] == "queued"


def test_sender_reports_empty_queue_before_provider_block(tmp_path):
    db = tmp_path / "control.db"
    _schema(db)
    provider = FakeProvider(ready=False)
    sender = ColdEmailSender(
        ColdEmailQueue(db), provider, EventJournal(tmp_path / "events.jsonl"), "email-w1"
    )
    assert sender.run_once() == "queue_empty"
    assert provider.sent == []


def test_sender_records_provider_confirmation_and_journal(tmp_path):
    db = tmp_path / "control.db"
    _schema(db)
    queue = ColdEmailQueue(db)
    queue.approve("c1", approved_by="operator")
    provider = FakeProvider()
    journal = EventJournal(tmp_path / "events.jsonl")
    sender = ColdEmailSender(queue, provider, journal, "email-w1")
    assert sender.run_once() == "sent"
    assert provider.sent == [("jobs@acme.test", "Engineer at Acme", "Hello Acme")]
    events = [json.loads(line)["event"] for line in journal.path.read_text().splitlines()]
    assert events == ["claimed", "sent"]


def test_gmail_provider_requires_token_and_returns_message_id(tmp_path):
    token = tmp_path / "google_token.json"
    calls = []

    def runner(args, **kwargs):
        calls.append((args, kwargs))
        class Result:
            returncode = 0
            stdout = '{"status":"sent","id":"gmail-42","threadId":"thread-1"}'
            stderr = ""
        return Result()

    script = tmp_path / "google_api.py"
    script.write_text("# installed wrapper")
    provider = GmailApiProvider(token, script, runner=runner)
    assert not provider.is_ready()
    token.write_text("{}")
    assert provider.is_ready()
    assert provider.send(to="jobs@acme.test", subject="Hello", body="Body") == "gmail-42"
    command = calls[0][0]
    assert command[-6:] == ["--to", "jobs@acme.test", "--subject", "Hello", "--body", "Body"]
    assert calls[0][1]["shell"] is False


def test_email_service_allows_only_token_refresh_under_read_only_home():
    unit = (Path(__file__).parents[1] / "deploy" / "jobhunt-email@.service").read_text()
    assert "ProtectHome=read-only" in unit
    assert "-/home/ubuntu/.hermes/google_token.json" in unit
    assert "ReadWritePaths=/home/ubuntu/.hermes\n" not in unit
