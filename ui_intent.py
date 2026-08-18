"""Intent-first UI clicks. Agents teach via learned/selectors.json — not hex classes."""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
_PATHS = (
    os.path.join(HERE, "learned", "selectors.json"),
    os.path.join(HERE, "learned", "selectors.example.json"),
)

def load_intents(portal: str) -> dict:
    data = {}
    for p in _PATHS:
        if os.path.isfile(p):
            try:
                data = json.load(open(p))
                break
            except Exception:
                continue
    return data.get(portal, {}) or {}

def click_intent(page, portal: str, intent: str, timeout_ms: int = 2500):
    """Try role/name, then text, then css. Return True if one hit."""
    for spec in load_intents(portal).get(intent, []):
        try:
            loc = None
            if spec.get("role") and spec.get("name"):
                loc = page.get_by_role(spec["role"], name=spec["name"], exact=False)
            elif spec.get("text"):
                loc = page.get_by_text(spec["text"], exact=False)
            elif spec.get("css"):
                loc = page.locator(spec["css"])
            if loc is None:
                continue
            el = loc.first
            if el.count() and el.is_visible():
                el.click(timeout=timeout_ms)
                return True
        except Exception:
            continue
    return False
