from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source(name: str) -> str:
    return (ROOT / name).read_text()


def assert_pinned_worker(name: str, portal: str, legacy: tuple[str, ...]) -> None:
    text = source(name)
    for marker in legacy:
        assert marker not in text
    assert f'current_session("{portal}")' in text
    assert "inject_current_session(" in text and f'"{portal}"' in text
    entrypoint = "def run_once" if "def run_once" in text else "def main"
    main_text = text[text.index(entrypoint):]
    assert main_text.index(f'current_session("{portal}")') < main_text.index("job = claim(")


def test_restart_always_workers_wait_when_publication_is_unavailable():
    for worker in (
        "worker_linkedin.py",
        "worker_wellfound.py",
        "worker_yc.py",
        "worker_external.py",
        "worker_review.py",
    ):
        text = source(worker)
        marker = "except PublicationUnavailable:"
        assert marker in text
        block = text.rsplit(marker, 1)[1].split("except ", 1)[0]
        assert "time.sleep(" in block and "continue" in block


def test_internshala_retries_readiness_without_systemd_restart_loop():
    text = source("worker_internshala.py")
    assert "def run_once():" in text
    main = text[text.index("def main():"):]
    assert "while True:" in main
    assert "run_once()" in main
    assert "time.sleep(300)" in main


def test_linkedin_uses_only_pinned_canonical_session():
    assert_pinned_worker("worker_linkedin.py", "linkedin", ("li_state.json",))


def test_wellfound_uses_only_pinned_canonical_session_and_disposable_profile():
    assert_pinned_worker(
        "worker_wellfound.py",
        "wellfound",
        ("portal_wellfound.json", "profiles/wf_w_"),
    )
    text = source("worker_wellfound.py")
    assert "tempfile.mkdtemp" in text and "shutil.rmtree(profile_dir" in text


def test_yc_uses_only_pinned_canonical_session_and_disposable_profiles():
    assert_pinned_worker("worker_yc.py", "yc", ("portal_yc.json", "profiles/yc_cap"))
    text = source("worker_yc.py")
    assert 'inject_current_session(' in text and '"yc"' in text
    assert "tempfile.mkdtemp" in text
    assert text.count("_open_runtime_context(p,") >= 2


def test_internshala_uses_only_pinned_canonical_session_and_disposable_profile():
    assert_pinned_worker(
        "worker_internshala.py",
        "internshala",
        ("portal_internshala.json", "profiles/is_login"),
    )
    text = source("worker_internshala.py")
    assert "tempfile.mkdtemp" in text and "shutil.rmtree(profile_dir" in text


def test_external_himalayas_path_uses_only_pinned_canonical_session():
    text = source("worker_external.py")
    assert "portal_himalayas.json" not in text
    assert "profiles/hima" not in text
    assert 'published_runtime(' in text
    assert '"himalayas" if requires_himalayas else None' in text
    assert 'inject_current_session(' in text and '"himalayas"' in text


def test_guard_is_probe_only_and_never_renews_or_reads_legacy_state():
    text = source("portal_guard.py")
    for marker in (
        "portal_wellfound.json",
        "portal_internshala.json",
        "portal_yc.json",
        "portal_himalayas.json",
        "def renew_",
        "li_session_guard.sh",
    ):
        assert marker not in text
    assert "record_health" in text
    assert "stage_candidate" not in text
    assert "promote(" not in text


def test_linkedin_renewal_publishes_through_candidate_probe_promotion():
    text = source("li_relogin.py")
    assert "li_state.json" not in text
    for marker in (
        "acquire_renewal",
        "stage_candidate",
        "record_probe",
        "promote",
        "release_renewal",
    ):
        assert marker in text
