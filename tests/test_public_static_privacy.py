from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "controlplane" / "static"
FORBIDDEN = ("Pratham", "Jaiswal", "prathamjaiswal204")


def test_dashboard_static_files_contain_no_applicant_identity():
    leaks = []
    for path in STATIC.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".html", ".js", ".css"}:
            continue
        text = path.read_text(encoding="utf-8")
        for marker in FORBIDDEN:
            if marker.lower() in text.lower():
                leaks.append(f"{path.relative_to(ROOT)}:{marker}")
    assert leaks == []
