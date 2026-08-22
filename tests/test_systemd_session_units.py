from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(name: str) -> str:
    return (ROOT / "systemd" / name).read_text()


def test_canonical_runtime_dropin_points_all_workers_to_production_state():
    env = text("jobhunt-canonical-runtime.env")
    dropin = text("canonical-runtime.conf")
    assert "JOBHUNT_QUEUE_DB=/var/lib/jobhunt/apply_queue.db" in env
    assert "JOBHUNT_CONTROL_DB=/var/lib/jobhunt/controlplane.db" in env
    assert "JOBHUNT_VAULT_KEY=/etc/jobhunt/controlplane.key" in env
    assert "JOBHUNT_SESSION_STORAGE=/home/ubuntu/job_hunt_linkedin/.private/sessions" in env
    assert "EnvironmentFile=/etc/jobhunt/canonical-runtime.env" in dropin


def test_session_probe_timer_is_read_only_and_bounded():
    service = text("jobhunt-session-probe.service")
    timer = text("jobhunt-session-probe.timer")
    assert "portal_guard.py" in service
    assert "Environment=JOBHUNT_CONTROL_DB=/var/lib/jobhunt/controlplane.db" in service
    assert "Environment=JOBHUNT_SESSION_STORAGE=/home/ubuntu/job_hunt_linkedin/.private/sessions" in service
    assert "Environment=JOBHUNT_VAULT_KEY=/etc/jobhunt/controlplane.key" in service
    assert "li_relogin.py" not in service
    assert "Restart=" not in service
    assert "OnUnitActiveSec=" in timer
    assert "Persistent=true" in timer


def test_linkedin_renewal_owner_is_manual_and_never_restart_loops():
    service = text("jobhunt-li-renew.service")
    assert "li_relogin.py" in service
    assert "Environment=JOBHUNT_CONTROL_DB=/var/lib/jobhunt/controlplane.db" in service
    assert "Environment=JOBHUNT_SESSION_STORAGE=/home/ubuntu/job_hunt_linkedin/.private/sessions" in service
    assert "Environment=JOBHUNT_VAULT_KEY=/etc/jobhunt/controlplane.key" in service
    assert "Restart=no" in service
    assert "TimeoutStartSec=" in service
    assert not (ROOT / "systemd" / "jobhunt-li-renew.timer").exists()
