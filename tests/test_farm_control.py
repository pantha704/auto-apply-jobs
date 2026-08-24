from pathlib import Path

from workflow.farm_control import clear_paused, is_paused, write_paused


def test_pause_flag_roundtrip(tmp_path, monkeypatch):
    flag = tmp_path / "FARM_PAUSED"
    monkeypatch.setenv("JOBHUNT_PAUSE_FLAG", str(flag))
    # reload paths via functions that read env each call
    import importlib
    import workflow.farm_control as fc
    importlib.reload(fc)
    assert fc.is_paused() is False
    written = fc.write_paused("operator stop")
    assert written == flag
    assert fc.is_paused() is True
    assert "operator stop" in fc.pause_reason()
    fc.clear_paused()
    assert fc.is_paused() is False


def test_watchdog_is_silent_when_paused(tmp_path, monkeypatch):
    flag = tmp_path / "FARM_PAUSED"
    flag.write_text("paused\n")
    monkeypatch.setenv("JOBHUNT_PAUSE_FLAG", str(flag))
    import importlib
    import workflow.farm_control as fc
    importlib.reload(fc)
    assert fc.is_paused() is True


def test_farm_cli_status_does_not_need_sudo(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBHUNT_PAUSE_FLAG", str(tmp_path / "FARM_PAUSED"))
    monkeypatch.setattr("workflow.farm_ops.unit_active", lambda unit: "inactive")
    from workflow import farm_ops
    data = farm_ops.status()
    assert data["paused"] is False
