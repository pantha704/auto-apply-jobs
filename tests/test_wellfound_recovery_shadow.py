import ast
import sqlite3
from pathlib import Path

import dynamic_ui
from workflow.schema import migrate_control


ROOT = Path(__file__).parents[1]


class Provider:
    def __init__(self):
        self.requests = []

    def recover(self, request):
        self.requests.append(request)
        from workflow.recovery import TraceAction
        return (TraceAction("click", request.intent, "c0", "button"),)


class Page:
    url = "https://wellfound.com/jobs/123"

    def evaluate(self, script, limit):
        return [
            {"id": "c0", "role": "button", "name": "Complex next step", "testid": "next"},
            {"id": "c1", "role": "textbox", "name": "Email", "value": "private@example.test"},
        ]


def test_dynamic_ui_browser_use_analysis_is_read_only_and_bounded():
    provider = Provider()
    result = dynamic_ui.browser_use_shadow_analysis(
        Page(), "wellfound", "open_apply_dialog", provider
    )
    assert result == {"candidate_id": "c0", "role": "button", "intent": "open_apply_dialog"}
    request = provider.requests[0]
    assert [candidate.candidate_id for candidate in request.candidates] == ["c0"]
    assert "private@example.test" not in repr(request)


def test_recovery_shadow_task_is_safe_and_deduplicated(tmp_path, monkeypatch):
    control = tmp_path / "control.db"
    migrate_control(control)
    monkeypatch.setenv("JOBHUNT_CONTROL_DB", str(control))
    proposal = {"candidate_id": "c0", "role": "button", "intent": "open_apply_dialog"}

    first = dynamic_ui.record_recovery_shadow_task("wellfound", proposal)
    second = dynamic_ui.record_recovery_shadow_task("wellfound", proposal)

    assert first == second
    with sqlite3.connect(control) as db:
        rows = db.execute(
            "SELECT type,status,safe_summary FROM operator_tasks"
        ).fetchall()
    assert rows == [
        (
            "recipe_drift",
            "open",
            "wellfound recovery shadow proposed c0 for open_apply_dialog",
        )
    ]


def test_wellfound_exposes_loopback_cdp_and_invokes_shadow_only_on_drift():
    source = (ROOT / "worker_wellfound.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert "--remote-debugging-address=127.0.0.1" in source
    assert "--remote-debugging-port=" in source
    assert "browser_use_shadow_analysis(" in source
    assert "JOBHUNT_RECOVERY_MODE" in source
    assert tree is not None
