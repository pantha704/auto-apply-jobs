"""Read-only, metadata-only analytics for the live queue database."""
from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_RANGES = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "1m": timedelta(days=30),
    "3m": timedelta(days=90),
    "6m": timedelta(days=183),
    "1y": timedelta(days=365),
    "all": None,
}
_BUCKETS = {"hourly", "daily", "weekly", "monthly"}
_DEFAULT_BUCKET = {
    "24h": "hourly", "7d": "daily", "1m": "daily", "3m": "weekly",
    "6m": "monthly", "1y": "monthly", "all": "monthly",
}
_TABLES = ("applications", "application_runs", "job_attempts", "jobs", "worker_instances")


def _utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f'PRAGMA table_info("{table}")')}


def _bucket_start(value: datetime, bucket: str) -> datetime:
    value = value.astimezone(timezone.utc)
    if bucket == "hourly":
        return value.replace(minute=0, second=0, microsecond=0)
    if bucket == "daily":
        return value.replace(hour=0, minute=0, second=0, microsecond=0)
    if bucket == "weekly":
        day = value.replace(hour=0, minute=0, second=0, microsecond=0)
        return day - timedelta(days=day.weekday())
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _duration_summary(values: list[int]) -> dict[str, int | None]:
    return {
        "count": len(values),
        "average": round(sum(values) / len(values)) if values else None,
        "minimum": min(values) if values else None,
        "maximum": max(values) if values else None,
    }


def aggregate_analytics(
    database: str | Path,
    range_name: str = "7d",
    *,
    now: datetime | None = None,
    bucket: str | None = None,
    custom_start: datetime | str | None = None,
    custom_end: datetime | str | None = None,
) -> dict[str, Any]:
    """Aggregate live queue metadata without returning job or applicant fields.

    The connection is opened read-only. Missing v2 tables/columns are treated as
    unavailable capabilities rather than synthesized data.
    """
    end = _utc(custom_end) if range_name == "custom" and custom_end else _utc(now or datetime.now(timezone.utc))
    if range_name == "custom":
        if custom_start is None:
            raise ValueError("custom_start is required for a custom range")
        start = _utc(custom_start)
        if start >= end:
            raise ValueError("custom_start must be before custom_end")
        days = (end - start).total_seconds() / 86400
        default_bucket = "hourly" if days <= 2 else "daily" if days <= 45 else "weekly" if days <= 120 else "monthly"
    else:
        if range_name not in _RANGES:
            raise ValueError(f"unsupported range: {range_name}")
        delta = _RANGES[range_name]
        start = end - delta if delta else None
        default_bucket = _DEFAULT_BUCKET[range_name]
    bucket = bucket or default_bucket
    if bucket not in _BUCKETS:
        raise ValueError(f"unsupported bucket: {bucket}")

    uri = Path(database).resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    try:
        existing = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        capabilities = {name: name in existing for name in _TABLES}
        cols = {name: _columns(db, name) if capabilities[name] else set() for name in _TABLES}

        def bounded(column: str) -> tuple[str, list[str]]:
            clauses, args = [f"datetime({column}) < datetime(?)"], [end.isoformat()]
            if start is not None:
                clauses.append(f"datetime({column}) >= datetime(?)")
                args.append(start.isoformat())
            return " AND ".join(clauses), args

        confirmed_times: list[datetime] = []
        # v2 is authoritative when available; legacy applications is the fallback.
        if capabilities["application_runs"] and {"confirmed", "started_at"} <= cols["application_runs"]:
            where, args = bounded("started_at")
            rows = db.execute(f"SELECT started_at FROM application_runs WHERE confirmed=1 AND {where}", args)
            confirmed_times = [_utc(row[0]) for row in rows]
        elif capabilities["applications"] and "applied_at" in cols["applications"]:
            where, args = bounded("applied_at")
            status = " AND status IN ('submitted','applied','success')" if "status" in cols["applications"] else ""
            rows = db.execute(f"SELECT applied_at FROM applications WHERE {where}{status}", args)
            confirmed_times = [_utc(row[0]) for row in rows]

        attempts = 0
        outcomes: Counter[str] = Counter()
        durations: list[int] = []
        if capabilities["job_attempts"] and "started_at" in cols["job_attempts"]:
            where, args = bounded("started_at")
            selected = [c for c in ("started_at", "finished_at", "outcome_code") if c in cols["job_attempts"]]
            rows = list(db.execute(f"SELECT {','.join(selected)} FROM job_attempts WHERE {where}", args))
            attempts = len(rows)
            for row in rows:
                if "outcome_code" in selected and row["outcome_code"]:
                    outcomes[str(row["outcome_code"])] += 1
                if "finished_at" in selected and row["finished_at"]:
                    durations.append(round((_utc(row["finished_at"]) - _utc(row["started_at"])).total_seconds() * 1000))
        elif capabilities["application_runs"] and "started_at" in cols["application_runs"]:
            where, args = bounded("started_at")
            if "outcome_code" in cols["application_runs"]:
                outcomes.update(str(r[0]) for r in db.execute(f"SELECT outcome_code FROM application_runs WHERE outcome_code IS NOT NULL AND {where}", args))

        portals: Counter[str] = Counter()
        if capabilities["application_runs"] and capabilities["jobs"] and "job_id" in cols["application_runs"] and "portal" in cols["jobs"]:
            where, args = bounded("r.started_at")
            portals.update(str(r[0] or "unknown") for r in db.execute(f"SELECT j.portal FROM application_runs r JOIN jobs j ON j.id=r.job_id WHERE {where}", args))
        elif capabilities["applications"] and {"portal", "applied_at"} <= cols["applications"]:
            where, args = bounded("applied_at")
            status = (
                " AND status IN ('submitted','applied','success')"
                if "status" in cols["applications"]
                else ""
            )
            portals.update(
                str(r[0] or "unknown")
                for r in db.execute(
                    f"SELECT portal FROM applications WHERE {where}{status}", args
                )
            )

        queue = {"pending": 0, "claimed": 0, "total": 0, "worker_reported": 0}
        expired = recoveries = 0
        if capabilities["jobs"] and "status" in cols["jobs"]:
            counts = Counter({str(r[0]): int(r[1]) for r in db.execute("SELECT status,count(*) FROM jobs GROUP BY status")})
            queue["pending"], queue["claimed"] = counts["pending"], counts["claimed"]
            queue["total"] = queue["pending"] + queue["claimed"]
            if "lease_expires_at" in cols["jobs"]:
                expired = db.execute("SELECT count(*) FROM jobs WHERE status='claimed' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?", (end.isoformat(),)).fetchone()[0]
            if "attempt_count" in cols["jobs"]:
                recoveries = db.execute("SELECT count(*) FROM jobs WHERE attempt_count > 1").fetchone()[0]

        workers: Counter[str] = Counter()
        if capabilities["worker_instances"]:
            if "state" in cols["worker_instances"]:
                workers.update({str(r[0]): int(r[1]) for r in db.execute("SELECT state,count(*) FROM worker_instances GROUP BY state")})
            if "queue_depth" in cols["worker_instances"]:
                queue["worker_reported"] = int(db.execute("SELECT coalesce(sum(queue_depth),0) FROM worker_instances").fetchone()[0])

        points = Counter(_bucket_start(t, bucket).isoformat() for t in confirmed_times)
        timeline = [{"start": key, "count": points[key]} for key in sorted(points)]
        return {
            "range": {"name": range_name, "start": start.isoformat() if start else None, "end": end.isoformat(), "bucket": bucket},
            "capabilities": capabilities,
            "confirmed_applications": len(confirmed_times),
            "timeline": timeline,
            "attempts": attempts,
            "outcomes": dict(sorted(outcomes.items())),
            "portals": dict(sorted(portals.items())),
            "queue_depth": queue,
            "success_rate": len(confirmed_times) / attempts if attempts else 0.0,
            "durations_ms": _duration_summary(durations),
            "leases": {"expired": int(expired), "recoveries": int(recoveries)},
            "workers": {"total": sum(workers.values()), "by_state": dict(sorted(workers.items()))},
        }
    finally:
        db.close()


get_analytics = aggregate_analytics
