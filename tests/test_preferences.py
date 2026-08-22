from __future__ import annotations

import sqlite3

from workflow.preferences import (
    PreferenceRepository,
    PreferenceRule,
    PreferenceSet,
    evaluate_preferences,
    normalize_job,
)
from workflow.schema import migrate_control


def test_hard_mismatch_excludes_and_trace_is_ordered():
    preferences = PreferenceSet(
        id="set-2",
        version=2,
        rules=(
            PreferenceRule("remote", "workplace", "hard", "eq", "remote", 0, "block", 2),
            PreferenceRule("salary", "salary_min", "soft", "gte", 100_000, 3, "review", 1),
        ),
    )

    result = evaluate_preferences(
        {"workplace": " On-Site ", "salary_min": "120000"}, preferences
    )

    assert result.preference_set_id == "set-2"
    assert result.preference_set_version == 2
    assert result.eligible is False
    assert result.score == 3.0
    assert [trace.rule_id for trace in result.trace] == ["salary", "remote"]
    assert result.trace[1].outcome == "mismatch"
    assert result.trace[1].effect == "excluded"


def test_unknown_policy_has_block_review_and_ignore_semantics():
    rules = tuple(
        PreferenceRule(name, name, "hard", "eq", True, 1, policy, ordinal)
        for ordinal, (name, policy) in enumerate(
            (("visa", "block"), ("clearance", "review"), ("travel", "ignore"))
        )
    )

    result = evaluate_preferences({}, PreferenceSet("set-1", 1, rules))

    assert result.eligible is False
    assert result.needs_review is True
    assert [item.effect for item in result.trace] == ["excluded", "review", "ignored"]


def test_soft_score_is_weighted_deterministic_and_none_has_no_effect():
    preferences = PreferenceSet(
        "set-1",
        1,
        (
            PreferenceRule("location", "location", "soft", "in", ["nyc", "remote"], 2.5, "ignore", 0),
            PreferenceRule("company", "company", "none", "eq", "Acme", 999, "block", 1),
            PreferenceRule("level", "level", "soft", "eq", "senior", 1.5, "ignore", 2),
        ),
    )

    first = evaluate_preferences({"location": "NYC", "company": "Acme", "level": "junior"}, preferences)
    second = evaluate_preferences({"level": "junior", "company": "Acme", "location": "NYC"}, preferences)

    assert first == second
    assert first.eligible is True
    assert first.score == 2.5
    assert first.max_score == 4.0
    assert first.trace[1].effect == "none"


def test_none_mode_never_affects_decision_even_when_value_is_unknown():
    preferences = PreferenceSet(
        "set-1",
        1,
        (PreferenceRule("ignored", "missing", "none", "eq", True, 99, "block", 0),),
    )

    result = evaluate_preferences({}, preferences)

    assert result.eligible is True
    assert result.needs_review is False
    assert result.score == 0
    assert result.max_score == 0
    assert result.trace[0].outcome == "unknown"
    assert result.trace[0].effect == "none"


def test_job_normalization_supports_nested_dict_and_common_numbers():
    job = normalize_job({"title": "  Senior Engineer ", "details": {"salary_min": "$125,000", "remote": "TRUE"}})

    assert job["title"] == "senior engineer"
    assert job["details.salary_min"] == 125000
    assert job["details.remote"] is True


def test_repository_activates_one_version_and_loads_rules(tmp_path):
    path = tmp_path / "control.db"
    sqlite3.connect(path).close()
    migrate_control(path)
    repository = PreferenceRepository(path)
    repository.create_set(
        version=1,
        rules=[{"criterion": "location", "mode": "hard", "operator": "eq", "expected": "remote", "weight": 0}],
        set_id="v1",
    )
    repository.create_set(
        version=2,
        rules=[{"criterion": "level", "mode": "soft", "operator": "eq", "expected": "senior", "weight": 4, "unknown_policy": "review"}],
        set_id="v2",
    )

    repository.activate(1)
    repository.activate(2)
    active = repository.get_active()

    assert active is not None
    assert active.id == "v2"
    assert active.version == 2
    assert active.rules[0].expected == "senior"
    db = sqlite3.connect(path)
    assert db.execute("SELECT version,status FROM preference_sets ORDER BY version").fetchall() == [(1, "superseded"), (2, "active")]
    db.close()
