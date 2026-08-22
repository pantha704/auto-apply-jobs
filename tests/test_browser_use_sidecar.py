import json
from pathlib import Path

import pytest

from workflow.browser_use_client import BrowserUseSidecar
from workflow.recovery import ActionCandidate, RecoveryRequest


def request():
    return RecoveryRequest(
        (ActionCandidate("c1", "button", "Complex next step"),),
        site_id="wellfound",
        intent="recover_navigation",
        page_fingerprint="sha256:test",
    )


def test_sidecar_receives_only_bounded_recovery_contract(tmp_path):
    captured = {}

    def fake_run(command, *, input_path, output_path, timeout, env):
        captured["payload"] = json.loads(Path(input_path).read_text())
        Path(output_path).write_text(json.dumps({"candidate_id": "c1"}))

    provider = BrowserUseSidecar(
        "http://127.0.0.1:9331",
        python="/isolated/bin/python",
        script="/app/browser_use_analyzer.py",
        run=fake_run,
        temp_root=tmp_path,
    )
    actions = provider.recover(request())

    assert actions[0].candidate_id == "c1"
    assert captured["payload"] == {
        "schema_version": 1,
        "site_id": "wellfound",
        "intent": "recover_navigation",
        "page_fingerprint": "sha256:test",
        "candidates": [{"candidate_id": "c1", "role": "button", "label": "Complex next step"}],
    }
    assert "value" not in json.dumps(captured["payload"])


def test_sidecar_rejects_unknown_candidate_id(tmp_path):
    def fake_run(command, *, input_path, output_path, timeout, env):
        Path(output_path).write_text(json.dumps({"candidate_id": "not-in-inventory"}))

    provider = BrowserUseSidecar(
        "http://127.0.0.1:9331",
        run=fake_run,
        temp_root=tmp_path,
    )
    with pytest.raises(ValueError, match="unknown candidate"):
        provider.recover(request())


def test_sidecar_none_result_produces_no_action(tmp_path):
    def fake_run(command, *, input_path, output_path, timeout, env):
        Path(output_path).write_text(json.dumps({"candidate_id": None}))

    provider = BrowserUseSidecar(
        "http://127.0.0.1:9331",
        run=fake_run,
        temp_root=tmp_path,
    )
    assert provider.recover(request()) == ()
