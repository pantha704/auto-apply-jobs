from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

from discovery.classify import classify_row

CONFIRMED = ("submitted", "applied")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def confirmed_count(queue_db: str) -> int:
    db = sqlite3.connect(queue_db)
    try:
        return db.execute(
            "SELECT COUNT(*) FROM applications WHERE status IN ('submitted','applied')"
        ).fetchone()[0]
    finally:
        db.close()


def _canonical_key(source_id: str, row: dict) -> str:
    email = str(row.get("email") or "").strip().lower()
    apply_url = str(row.get("apply_url") or "").strip()
    careers = str(row.get("careers_url") or row.get("website") or "").strip()
    company = str(row.get("company") or "").strip().lower()
    raw = "|".join((source_id, email, apply_url, careers, company))
    return hashlib.sha256(raw.encode()).hexdigest()


def _supported_external_source(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    if host == "weworkremotely.com" or host.endswith(".weworkremotely.com"):
        return "weworkremotely"
    if host == "himalayas.app" or host.endswith(".himalayas.app"):
        return "himalayas"
    return None


def _queue_external_job(
    db: sqlite3.Connection, url: str, title: str, source: str, now: str
) -> bool:
    job_id = "ext-" + hashlib.sha256(url.encode()).hexdigest()[:24]
    cursor = db.execute(
        """INSERT OR IGNORE INTO queue.jobs(
             id,portal,url,title,source,status,prio,fetched_at
           ) VALUES(?,'external',?,?,?,'pending',1,?)""",
        (job_id, url, title or "External opportunity", source, now),
    )
    return cursor.rowcount == 1


def apply_batch(control_db: str, queue_db: str, source_id: str, entities: list[dict]) -> dict:
    if len(entities) > 200:
        raise ValueError("too_many_entities")
    before = confirmed_count(queue_db)
    counts = {
        "accepted": 0,
        "routed_apply": 0,
        "routed_watchlist": 0,
        "routed_email": 0,
        "routed_review": 0,
        "dropped": 0,
    }
    now = _now()
    db = sqlite3.connect(control_db)
    try:
        db.execute("ATTACH DATABASE ? AS queue", (queue_db,))
        db.execute("BEGIN IMMEDIATE")
        src = db.execute("SELECT id FROM external_sources WHERE id=?", (source_id,)).fetchone()
        if not src:
            raise KeyError("unknown_source")
        for row in entities:
            routes = classify_row(row)
            if not routes:
                counts["dropped"] += 1
                continue
            key = _canonical_key(source_id, row)
            email = str(row.get("email") or "").strip()
            email_norm = email.lower()
            website = str(row.get("website") or "").strip()
            careers = str(row.get("careers_url") or "").strip() or website
            apply_url = str(row.get("apply_url") or "").strip()
            company = str(row.get("company") or "").strip()
            for route in routes:
                routed = route["routed"]
                if routed == "apply_candidate":
                    routed = "apply"
                external_source = None
                if routed == "apply":
                    external_source = _supported_external_source(apply_url)
                    if external_source is None:
                        routed = "review"
                entity_id = str(uuid.uuid4())
                kind = {
                    "apply": "job",
                    "watchlist": "company",
                    "cold_email": "email",
                    "review": "unknown",
                    "dropped": "unknown",
                }.get(routed, "unknown")
                inserted = db.execute(
                    """INSERT OR IGNORE INTO extracted_entities(
                        id,source_id,company,website,careers_url,apply_url,email,role,location,requirements,
                        entity_kind,canonical_key,raw_json,routed,routed_at,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        entity_id,
                        source_id,
                        company,
                        website,
                        careers,
                        apply_url,
                        email,
                        str(row.get("role") or ""),
                        str(row.get("location") or ""),
                        str(row.get("requirements") or ""),
                        kind,
                        f"{key}:{routed}",
                        json.dumps({k: row.get(k) for k in row if k != "email"}),
                        routed if routed in {"apply", "watchlist", "cold_email", "review", "dropped"} else "review",
                        now,
                        now,
                    ),
                ).rowcount == 1
                if routed == "cold_email" and email_norm:
                    db.execute(
                        """INSERT INTO cold_contacts(
                            id,company,email,email_norm,website,role,requirements,source_id,extracted_id,status,created_at,updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,'queued',?,?)
                        ON CONFLICT(email_norm) DO UPDATE SET
                          company=excluded.company,
                          role=excluded.role,
                          requirements=excluded.requirements,
                          updated_at=excluded.updated_at""",
                        (
                            str(uuid.uuid4()),
                            company,
                            email,
                            email_norm,
                            website,
                            str(row.get("role") or ""),
                            str(row.get("requirements") or ""),
                            source_id,
                            entity_id,
                            now,
                            now,
                        ),
                    )
                    counts["routed_email"] += 1
                elif routed == "watchlist" and len(careers) > 8:
                    db.execute(
                        """INSERT OR IGNORE INTO company_watchlist(
                            id,company,website,careers_url,source_id,status,created_at
                        ) VALUES(?,?,?,?,?,'pending',?)""",
                        (str(uuid.uuid4()), company, website, careers, source_id, now),
                    )
                    counts["routed_watchlist"] += 1
                elif routed == "review":
                    if inserted and apply_url:
                        db.execute(
                            """INSERT INTO operator_tasks(
                                 id,type,status,safe_summary,created_at
                               ) VALUES(?,'manual_review','open',?,?)""",
                            (str(uuid.uuid4()), "External apply URL needs an adapter", now),
                        )
                    counts["routed_review"] += 1
                elif routed == "apply":
                    assert external_source is not None
                    _queue_external_job(
                        db,
                        apply_url,
                        str(row.get("role") or row.get("title") or "External opportunity"),
                        external_source,
                        now,
                    )
                    counts["routed_apply"] += 1
                else:
                    counts["dropped"] += 1
            counts["accepted"] += 1
        db.execute(
            "UPDATE external_sources SET status='ready', last_ingested_at=?, last_error=NULL, error_count=0, updated_at=? WHERE id=?",
            (now, now, source_id),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    after = confirmed_count(queue_db)
    return {
        "accepted": counts["accepted"],
        "routed_apply": counts["routed_apply"],
        "routed_watchlist": counts["routed_watchlist"],
        "routed_email": counts["routed_email"],
        "routed_review": counts["routed_review"],
        "dropped": counts["dropped"],
        "confirmed_before": before,
        "confirmed_after": after,
    }
