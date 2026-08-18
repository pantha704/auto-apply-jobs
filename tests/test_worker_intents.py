"""Static contract checks for the first intent-driven worker slice."""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_first_slice_workers_import_and_call_dynamic_ui():
    for filename in [
        "worker_internshala.py",
        "worker_linkedin.py",
        "worker_yc.py",
        "worker_external.py",
    ]:
        tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
        source = (ROOT / filename).read_text(encoding="utf-8")
        assert any(
            isinstance(node, ast.Import) and any(alias.name == "dynamic_ui" for alias in node.names)
            for node in tree.body
        ), filename
        assert "dynamic_ui.click(" in source, filename


def test_selector_maps_are_valid_json_and_cover_first_slice():
    data = json.loads((ROOT / "learned" / "selectors.json").read_text(encoding="utf-8"))
    for portal, intent in [
        ("internshala", "apply"),
        ("internshala", "submit"),
        ("linkedin", "easy_apply"),
        ("linkedin", "submit"),
        ("yc", "apply"),
        ("yc", "send"),
        ("himalayas", "apply"),
        ("himalayas", "submit"),
    ]:
        assert data[portal][intent], (portal, intent)
