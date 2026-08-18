#!/usr/bin/env python3
"""Dynamic UI navigator — intent first, CSS last.

Sites change class names. Agents teach via learned/selectors.json.
Workers call click/fill; on miss they dump an a11y snapshot and return False
so the job can be marked needs-agent:<intent> instead of a wrong click.
"""
from __future__ import annotations

import json, os, re, time
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
LEARNED = os.path.join(HERE, "learned", "selectors.json")
EXAMPLE = os.path.join(HERE, "learned", "selectors.example.json")
INBOX = os.path.join(HERE, "learned", "agent_inbox")


def _load() -> dict:
    for p in (LEARNED, EXAMPLE):
        if os.path.isfile(p):
            try:
                return json.load(open(p))
            except Exception:
                continue
    return {}


def intents(portal: str, intent: str) -> list[dict]:
    return list((_load().get(portal) or {}).get(intent) or [])


def _locators(page, spec: dict):
    """Yield Playwright locators from one intent spec, most stable first."""
    role, name = spec.get("role"), spec.get("name")
    text, css = spec.get("text"), spec.get("css")
    if role and name:
        yield page.get_by_role(role, name=re.compile(name, re.I))
    if name and not role:
        yield page.get_by_role("button", name=re.compile(name, re.I))
        yield page.get_by_label(re.compile(name, re.I))
    if text:
        yield page.get_by_text(re.compile(re.escape(text), re.I))
        yield page.locator(f"button:has-text('{text}')")
        yield page.locator(f"a:has-text('{text}')")
    if css:
        yield page.locator(css)


def resolve(page, portal: str, intent: str, timeout_ms: int = 2000):
    """Return first visible locator matching the intent, or None."""
    for spec in intents(portal, intent):
        for loc in _locators(page, spec):
            try:
                el = loc.first
                if el.count() and el.is_visible():
                    return el
            except Exception:
                continue
    return None


def click(page, portal: str, intent: str, timeout_ms: int = 3000) -> bool:
    el = resolve(page, portal, intent, timeout_ms)
    if el is None:
        spec = llm_pick(page, portal, intent)
        if spec:
            remember(portal, intent, spec)
            for loc in _locators(page, spec):
                try:
                    el = loc.first
                    if el.count() and el.is_visible():
                        break
                    el = None
                except Exception:
                    el = None
        if el is None:
            return False
    try:
        el.click(timeout=timeout_ms)
        return True
    except Exception:
        try:
            el.click(timeout=timeout_ms, force=True)
            return True
        except Exception:
            return False


def fill(page, portal: str, intent: str, value: str, timeout_ms: int = 3000) -> bool:
    el = resolve(page, portal, intent, timeout_ms)
    if el is None:
        return False
    try:
        el.fill(value, timeout=timeout_ms)
        return True
    except Exception:
        return False


def snapshot_a11y(page, limit: int = 80) -> list[dict[str, Any]]:
    """Compact accessibility inventory for an agent (no full HTML)."""
    try:
        return page.evaluate(
            """(limit) => {
              const roles = ['button','link','textbox','combobox','radio','checkbox','tab','menuitem'];
              const out = [];
              const walk = (root) => {
                const nodes = root.querySelectorAll('button,a,input,textarea,select,[role]');
                for (const el of nodes) {
                  if (out.length >= limit) return;
                  const r = el.getAttribute('role') || el.tagName.toLowerCase();
                  const name = (el.getAttribute('aria-label') || el.innerText || el.value || '').trim().slice(0, 80);
                  if (!name && r === 'div') continue;
                  const box = el.getBoundingClientRect();
                  if (box.width < 2 || box.height < 2) continue;
                  out.push({role: r, name, tag: el.tagName.toLowerCase(),
                            testid: el.getAttribute('data-test') || el.getAttribute('data-testid') || ''});
                }
              };
              walk(document);
              return out;
            }""",
            limit,
        )
    except Exception as e:
        return [{"error": str(e)[:200]}]


def report_miss(page, portal: str, intent: str, url: str = "") -> str:
    """Write inbox item + screenshot. Return needs-agent reason string."""
    os.makedirs(INBOX, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    png = os.path.join(INBOX, f"{portal}_{intent}_{ts}.png")
    meta = os.path.join(INBOX, f"{portal}_{intent}_{ts}.json")
    try:
        page.screenshot(path=png, full_page=False)
    except Exception:
        png = ""
    rec = {
        "portal": portal,
        "intent": intent,
        "url": url or getattr(page, "url", ""),
        "ts": ts,
        "a11y": snapshot_a11y(page),
        "screenshot": png,
    }
    try:
        json.dump(rec, open(meta, "w"), indent=2)
    except Exception:
        pass
    return f"needs-agent:{intent}"


def remember(portal: str, intent: str, spec: dict) -> None:
    """Append a learned spec so the next job does not pay for the LLM again."""
    if not spec:
        return
    data = _load()
    bucket = data.setdefault(portal, {}).setdefault(intent, [])
    if spec not in bucket:
        bucket.insert(0, spec)
    os.makedirs(os.path.dirname(LEARNED), exist_ok=True)
    try:
        json.dump(data, open(LEARNED, "w"), indent=2)
    except Exception:
        pass


def llm_pick(page, portal: str, intent: str) -> dict | None:
    """Cheap OpenAI-compatible model picks ONE visible control for an intent.

    Default: Groq llama-3.1-8b-instant (~$0.05 / $0.08 per M tokens).
    Env: GROQ_API_KEY or UI_LLM_API_KEY
         UI_LLM_BASE (default https://api.groq.com/openai/v1)
         UI_LLM_MODEL (default llama-3.1-8b-instant)
    Never used for form answers — only {role,name} / {text} / {css}.
    """
    key = os.environ.get("GROQ_API_KEY") or os.environ.get("UI_LLM_API_KEY") or ""
    if not key or os.environ.get("UI_LLM") == "0":
        return None
    tree = snapshot_a11y(page, limit=40)
    if not tree or tree[0].get("error"):
        return None
    body = {
        "model": os.environ.get("UI_LLM_MODEL", "llama-3.1-8b-instant"),
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "Pick the single control that fulfills the UI intent. "
                    "Return JSON only: {\"role\":\"button\",\"name\":\"...\"} "
                    "or {\"text\":\"...\"} or {\"css\":\"...\"} or {\"none\":true}. "
                    "Use a name that appears in the inventory. Never invent answers."
                ),
            },
            {
                "role": "user",
                "content": json.dumps({"portal": portal, "intent": intent, "controls": tree}),
            },
        ],
    }
    try:
        import urllib.request
        base = os.environ.get("UI_LLM_BASE", "https://api.groq.com/openai/v1").rstrip("/")
        req = urllib.request.Request(
            base + "/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            method="POST",
        )
        raw = json.loads(urllib.request.urlopen(req, timeout=12).read().decode())
        txt = raw["choices"][0]["message"]["content"]
        spec = json.loads(txt)
        if spec.get("none") or not (spec.get("name") or spec.get("text") or spec.get("css")):
            return None
        return {k: spec[k] for k in ("role", "name", "text", "css") if spec.get(k)}
    except Exception:
        return None
