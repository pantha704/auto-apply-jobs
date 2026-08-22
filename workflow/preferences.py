"""Deterministic, explainable evaluation of versioned job preferences."""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_MISSING = object()
_VALID_MODES = {"hard", "soft", "none"}
_VALID_UNKNOWN = {"block", "review", "ignore"}


@dataclass(frozen=True)
class PreferenceRule:
    id: str
    criterion: str
    mode: str
    operator: str
    expected: Any
    weight: float = 0.0
    unknown_policy: str = "block"
    ordinal: int = 0

    def __post_init__(self) -> None:
        if self.mode not in _VALID_MODES:
            raise ValueError(f"invalid preference mode: {self.mode}")
        if self.unknown_policy not in _VALID_UNKNOWN:
            raise ValueError(f"invalid unknown policy: {self.unknown_policy}")
        if self.weight < 0:
            raise ValueError("weight must be non-negative")


@dataclass(frozen=True)
class PreferenceSet:
    id: str
    version: int
    rules: tuple[PreferenceRule, ...]
    status: str = "active"


@dataclass(frozen=True)
class RuleTrace:
    rule_id: str
    criterion: str
    mode: str
    operator: str
    expected: Any
    actual: Any
    outcome: str
    effect: str
    score_delta: float
    reason: str


@dataclass(frozen=True)
class PreferenceEvaluation:
    preference_set_id: str
    preference_set_version: int
    eligible: bool
    needs_review: bool
    score: float
    max_score: float
    trace: tuple[RuleTrace, ...]


def _scalar(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    value = value.strip()
    lowered = value.casefold()
    if lowered in {"true", "yes"}:
        return True
    if lowered in {"false", "no"}:
        return False
    numeric = re.fullmatch(r"[$€£]?\s*([+-]?[\d,]+(?:\.\d+)?)\s*%?", value)
    if numeric:
        number = float(numeric.group(1).replace(",", ""))
        return int(number) if number.is_integer() else number
    return lowered


def normalize_job(job: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten nested mappings and normalize scalar values without mutating input."""
    normalized: dict[str, Any] = {}

    def visit(prefix: str, value: Any) -> None:
        if isinstance(value, Mapping):
            for key in sorted(value, key=str):
                name = f"{prefix}.{key}" if prefix else str(key)
                visit(name, value[key])
        elif isinstance(value, (list, tuple, set)):
            normalized[prefix] = tuple(_scalar(item) for item in value)
        else:
            normalized[prefix] = _scalar(value)

    visit("", job)
    return normalized


def _matches(operator: str, actual: Any, expected: Any) -> bool:
    actual = _scalar(actual)
    if isinstance(expected, list):
        expected = tuple(_scalar(item) for item in expected)
    elif isinstance(expected, tuple):
        expected = tuple(_scalar(item) for item in expected)
    else:
        expected = _scalar(expected)
    if operator in {"eq", "equals", "=="}:
        return actual == expected
    if operator in {"neq", "not_equals", "!="}:
        return actual != expected
    if operator == "in":
        return actual in expected
    if operator == "not_in":
        return actual not in expected
    if operator == "contains":
        return expected in actual
    if operator in {"gte", ">="}:
        return actual >= expected
    if operator in {"lte", "<="}:
        return actual <= expected
    if operator in {"gt", ">"}:
        return actual > expected
    if operator in {"lt", "<"}:
        return actual < expected
    raise ValueError(f"unsupported preference operator: {operator}")


def evaluate_preferences(job: Mapping[str, Any], preferences: PreferenceSet) -> PreferenceEvaluation:
    values = normalize_job(job)
    traces: list[RuleTrace] = []
    eligible, review, score = True, False, 0.0
    ordered = sorted(preferences.rules, key=lambda rule: (rule.ordinal, rule.id))
    max_score = sum(float(rule.weight) for rule in ordered if rule.mode == "soft")
    for rule in ordered:
        actual = values.get(rule.criterion, _MISSING)
        if actual is _MISSING or actual is None:
            if rule.mode == "none":
                effect = "none"
            else:
                effect = {"block": "excluded", "review": "review", "ignore": "ignored"}[rule.unknown_policy]
                eligible = eligible and effect != "excluded"
                review = review or effect == "review"
            traces.append(RuleTrace(rule.id, rule.criterion, rule.mode, rule.operator, rule.expected, None, "unknown", effect, 0.0, f"value unknown; policy={rule.unknown_policy}"))
            continue
        matched = _matches(rule.operator, actual, rule.expected)
        delta = float(rule.weight) if rule.mode == "soft" and matched else 0.0
        score += delta
        if rule.mode == "none":
            effect = "none"
        elif rule.mode == "hard" and not matched:
            effect, eligible = "excluded", False
        elif rule.mode == "soft" and matched:
            effect = "scored"
        else:
            effect = "satisfied" if matched else "no_score"
        traces.append(RuleTrace(rule.id, rule.criterion, rule.mode, rule.operator, rule.expected, actual, "match" if matched else "mismatch", effect, delta, "operator matched" if matched else "operator did not match"))
    return PreferenceEvaluation(preferences.id, preferences.version, eligible, review, score, max_score, tuple(traces))


class PreferenceRepository:
    """SQLite persistence over the normalized preference_sets/preference_rules schema."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def create_set(self, *, version: int, rules: Iterable[Mapping[str, Any] | PreferenceRule], set_id: str | None = None) -> PreferenceSet:
        set_id = set_id or str(uuid.uuid4())
        materialized: list[PreferenceRule] = []
        for ordinal, item in enumerate(rules):
            if isinstance(item, PreferenceRule):
                rule = item
            else:
                criterion = str(item["criterion"])
                rule = PreferenceRule(str(item.get("id", f"{set_id}:{ordinal}:{criterion}")), criterion, str(item["mode"]), str(item["operator"]), item.get("expected", item.get("expected_json")), float(item.get("weight", 0)), str(item.get("unknown_policy", "block")), int(item.get("ordinal", ordinal)))
            materialized.append(rule)
        created = datetime.now(timezone.utc).isoformat()
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO preference_sets(id,version,status,created_at) VALUES(?,?,'draft',?)", (set_id, version, created))
            db.executemany("INSERT INTO preference_rules(id,preference_set_id,criterion,mode,operator,expected_json,weight,unknown_policy,ordinal) VALUES(?,?,?,?,?,?,?,?,?)", [(r.id, set_id, r.criterion, r.mode, r.operator, json.dumps(r.expected, sort_keys=True, separators=(",", ":")), r.weight, r.unknown_policy, r.ordinal) for r in materialized])
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return PreferenceSet(set_id, version, tuple(sorted(materialized, key=lambda r: (r.ordinal, r.id))), "draft")

    def activate(self, version: int) -> PreferenceSet:
        now = datetime.now(timezone.utc).isoformat()
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id FROM preference_sets WHERE version=?", (version,)).fetchone()
            if row is None:
                raise LookupError(f"preference set version {version} does not exist")
            db.execute("UPDATE preference_sets SET status='superseded' WHERE status='active' AND version<>?", (version,))
            db.execute("UPDATE preference_sets SET status='active',activated_at=? WHERE version=?", (now, version))
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        result = self.get_version(version)
        assert result is not None
        return result

    def get_version(self, version: int) -> PreferenceSet | None:
        db = self._connect()
        try:
            row = db.execute("SELECT id,version,status FROM preference_sets WHERE version=?", (version,)).fetchone()
            if row is None:
                return None
            rules = db.execute("SELECT id,criterion,mode,operator,expected_json,weight,unknown_policy,ordinal FROM preference_rules WHERE preference_set_id=? ORDER BY ordinal,id", (row["id"],)).fetchall()
            return PreferenceSet(row["id"], row["version"], tuple(PreferenceRule(r["id"], r["criterion"], r["mode"], r["operator"], json.loads(r["expected_json"]), r["weight"], r["unknown_policy"], r["ordinal"]) for r in rules), row["status"])
        finally:
            db.close()

    def get_active(self) -> PreferenceSet | None:
        db = self._connect()
        try:
            row = db.execute("SELECT version FROM preference_sets WHERE status='active' ORDER BY version DESC LIMIT 1").fetchone()
        finally:
            db.close()
        return None if row is None else self.get_version(int(row["version"]))

    def evaluate_active(self, job: Mapping[str, Any]) -> PreferenceEvaluation:
        active = self.get_active()
        if active is None:
            raise LookupError("no active preference set")
        return evaluate_preferences(job, active)
