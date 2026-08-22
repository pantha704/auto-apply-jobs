#!/usr/bin/env python3
"""Dynamic UI navigator — intent first, CSS last.

Sites change class names. Agents teach via learned/selectors.json.
Workers call click/fill; on miss they dump an a11y snapshot and return False
so the job can be marked needs-agent:<intent> instead of a wrong click.
"""
from __future__ import annotations

import json, os, re, tempfile, time
from pathlib import Path
from threading import Lock
from typing import Any, Callable

HERE = os.path.dirname(os.path.abspath(__file__))
LEARNED = os.path.join(HERE, "learned", "selectors.json")
EXAMPLE = os.path.join(HERE, "learned", "selectors.example.json")
INBOX = os.path.join(HERE, "learned", "agent_inbox")


_WRITE_LOCK = Lock()
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d ()-]{8,}\d)(?!\d)")
_TOKEN_RE = re.compile(r"(?i)(?:bearer\s+|token[=:]\s*|cookie[=:]\s*)[^\s,;]+")


def _redact(value: Any) -> str:
    text = str(value or "")
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
    text = _TOKEN_RE.sub("[REDACTED_TOKEN]", text)
    return text[:120]


def sanitize_controls(controls: list[dict]) -> list[dict]:
    """Return only safe, actionable metadata; never serialize form values."""
    allowed = {"button", "link", "checkbox", "radio", "tab", "combobox", "menuitem"}
    safe: list[dict] = []
    for index, item in enumerate(controls or []):
        role = str(item.get("role") or "").lower()
        if role not in allowed:
            continue
        name = _redact(item.get("name"))
        if not name:
            continue
        safe.append({
            "id": str(item.get("id") or f"c{index}"),
            "role": role,
            "name": name,
            "testid": _redact(item.get("testid")),
        })
    return safe[:80]


def validate_candidate(candidate: dict, controls: list[dict]) -> dict:
    """Resolve a model candidate only against the supplied inventory."""
    if not isinstance(candidate, dict) or set(candidate) != {"candidate_id"}:
        raise ValueError("candidate must contain only candidate_id")
    candidate_id = candidate.get("candidate_id")
    for control in controls:
        if control.get("id") == candidate_id:
            return control
    raise ValueError("unknown candidate_id")


def validate_action_candidate(candidate: dict, controls: list[dict], *, intent: str, source: str) -> dict:
    if intent in {"submit", "send"} and source == "llm":
        raise ValueError("LLM cannot choose a submit/send control")
    return validate_candidate(candidate, controls)


def _atomic_json_write(path: str, data: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".selectors.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def remember_after_verified(portal: str, intent: str, spec: dict, *, verified: bool) -> bool:
    """Persist learning only after an observed postcondition."""
    if not verified or not isinstance(spec, dict):
        return False
    if spec.get("source") not in {"human", "agent"}:
        return False
    with _WRITE_LOCK:
        data = _load()
        bucket = data.setdefault(portal, {}).setdefault(intent, [])
        if spec not in bucket:
            bucket.insert(0, spec)
            _atomic_json_write(LEARNED, data)
    return True


def llm_pick_candidate(controls: list[dict], portal: str, intent: str, transport: Callable) -> dict:
    """Ask an OpenAI-compatible model for a candidate ID, never a selector."""
    safe = sanitize_controls(controls)
    if not safe:
        return {"none": True}
    body = {
        "model": os.environ.get("UI_LLM_MODEL", "llama-3.1-8b-instant"),
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content":
             "Choose one candidate_id for the requested low-risk UI intent. "
             "Return only {candidate_id} or {none:true}. Never return CSS, XPath, "
             "text, answers, or any other fields."},
            {"role": "user", "content": json.dumps({
                "portal": portal, "intent": intent, "controls": safe
            })},
        ],
    }
    result = transport(body)
    if not isinstance(result, dict) or result.get("none"):
        return {"none": True}
    return {"candidate_id": result.get("candidate_id")}



def _load() -> dict:
    for p in (LEARNED, EXAMPLE):
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as handle:
                    return json.load(handle)
            except (OSError, ValueError, TypeError):
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


def snapshot_actionable_controls(page, limit: int = 80) -> list[dict]:
    """Return local control metadata without form values or hidden content."""
    try:
        return page.evaluate(
            """(limit) => {
              const allowed = new Set(['BUTTON','A']);
              const out = [];
              for (const el of document.querySelectorAll('button,a,[role]')) {
                if (out.length >= limit) break;
                const role = (el.getAttribute('role') || (el.tagName === 'A' ? 'link' : 'button')).toLowerCase();
                if (!['button','link','checkbox','radio','tab','combobox','menuitem'].includes(role)) continue;
                const box = el.getBoundingClientRect();
                if (box.width < 2 || box.height < 2) continue;
                const name = (el.getAttribute('aria-label') || el.innerText || '').trim().slice(0, 120);
                if (!name) continue;
                out.push({id: `c${out.length}`, role, name,
                  testid: el.getAttribute('data-testid') || el.getAttribute('data-test') || ''});
              }
              return out;
            }""",
            limit,
        )
    except Exception:
        return []


def _llm_transport(body: dict) -> dict:
    import urllib.request
    key = os.environ.get("GROQ_API_KEY") or os.environ.get("UI_LLM_API_KEY")
    if not key or os.environ.get("UI_LLM") == "0":
        return {"none": True}
    endpoint = "https://api.groq.com/openai/v1/chat/completions"
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    raw = json.loads(urllib.request.urlopen(req, timeout=12).read().decode())  # nosec B310: fixed HTTPS Groq endpoint
    return json.loads(raw["choices"][0]["message"]["content"])


def click(page, portal: str, intent: str, timeout_ms: int = 3000) -> bool:
    """Click a trusted intent; optional LLM may choose only a low-risk candidate."""
    el = resolve(page, portal, intent, timeout_ms)
    if el is None and intent not in {"submit", "send"}:
        controls = snapshot_actionable_controls(page)
        try:
            candidate = llm_pick_candidate(controls, portal, intent, _llm_transport)
            control = validate_action_candidate(candidate, controls, intent=intent, source="llm")
            for loc in _locators(page, {"role": control["role"], "name": control["name"]}):
                try:
                    candidate_el = loc.first
                    if candidate_el.count() and candidate_el.is_visible():
                        el = candidate_el
                        break
                except Exception:
                    continue
        except Exception:
            el = None
    if el is None:
        return False
    try:
        el.click(timeout=timeout_ms)
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
            r"""(limit) => {
              const roles = ['button','link','textbox','combobox','radio','checkbox','tab','menuitem'];
              const out = [];
              const walk = (root) => {
                const nodes = root.querySelectorAll('button,a,input,textarea,select,[role]');
                for (const el of nodes) {
                  if (out.length >= limit) return;
                  const r = el.getAttribute('role') || el.tagName.toLowerCase();
                  // Form controls use labels/placeholders only; never values or rendered answers.
                  const isForm = ['input','textarea','select'].includes(el.tagName.toLowerCase());
                  const raw = isForm
                    ? (el.getAttribute('aria-label') || el.labels?.[0]?.innerText || el.getAttribute('placeholder') || '')
                    : (el.getAttribute('aria-label') || el.innerText || '');
                  const redact = (text) => text.trim().slice(0, 80)
                    .replace(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, '[REDACTED_EMAIL]')
                    .replace(/(?:\+?\d[\d\s().-]{7,}\d)/g, '[REDACTED_PHONE]')
                    .replace(/\b\d{4,8}\b/g, '[REDACTED_NUMBER]')
                    .replace(/https?:\/\/\S+/g, '[REDACTED_URL]');
                  const name = redact(raw);
                  if (!name && r === 'div') continue;
                  const box = el.getBoundingClientRect();
                  if (box.width < 2 || box.height < 2) continue;
                  out.push({role: r, name, tag: el.tagName.toLowerCase(),
                            testid: redact(el.getAttribute('data-test') || el.getAttribute('data-testid') || '')});
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
    blur_style = None
    try:
        blur_style = page.add_style_tag(content="input,textarea,select,[contenteditable='true']{filter:blur(10px)!important;color:transparent!important}")
        page.screenshot(path=png, full_page=False)
    except Exception:
        png = ""
    finally:
        if blur_style is not None:
            try:
                blur_style.evaluate("el => el.remove()")
            except Exception:
                pass
    rec = {
        "portal": portal,
        "intent": intent,
        "url": (url or getattr(page, "url", "")).split("?", 1)[0].split("#", 1)[0],
        "ts": ts,
        "a11y": snapshot_a11y(page),
        "screenshot": png,
    }
    try:
        json.dump(rec, open(meta, "w"), indent=2)
    except Exception:
        pass
    return f"needs-agent:{intent}"


def remember(portal: str, intent: str, spec: dict, *, verified: bool = False) -> bool:
    """Compatibility wrapper; callers must explicitly prove verification."""
    return remember_after_verified(portal, intent, spec, verified=verified)


def hybrid_click(page, portal: str, intent: str, cdp_url: str, *, postcondition=None) -> bool:
    """Route a low-risk click through deterministic Playwright, then BU recovery."""
    if intent in {"submit", "send", "finalize"}:
        raise ValueError("terminal actions are deterministic-only")
    from workflow.adapters.base import DriverAction, RuntimeContext
    from workflow.browser_use_client import BrowserUseSidecar
    from workflow.leases import FileSessionLease
    from workflow.page_runtime import PlaywrightPageDriver
    from workflow.runner import RuntimeRouter

    class Adapter:
        name = portal

        def plan(self, candidates, context):
            for spec in intents(portal, intent):
                wanted = str(spec.get("name") or spec.get("text") or "").casefold()
                for candidate in candidates:
                    if wanted and wanted in candidate.label.casefold():
                        return (DriverAction("click", intent, candidate.candidate_id),)
            return ()

    def verify_postcondition() -> bool:
        if postcondition is None:
            return True
        for _ in range(25):
            try:
                if postcondition():
                    return True
            except Exception:
                pass
            try:
                page.wait_for_timeout(100)
            except Exception:
                time.sleep(0.1)
        return False

    driver = PlaywrightPageDriver(page)
    router = RuntimeRouter(
        driver,
        FileSessionLease(),
        Adapter(),
        BrowserUseSidecar(cdp_url),
        recovery_mode="execute",
        recovery_verifier=lambda _context, _trace: verify_postcondition(),
    )
    result = router.run(
        RuntimeContext(f"{portal}:{intent}", getattr(page, "url", "about:blank")),
        recovery_intent=intent,
    )
    return result.route.value in {"recipe", "deterministic", "recovery"}


def browser_use_shadow_analysis(page, portal: str, intent: str, provider) -> dict | None:
    from workflow.recovery import ActionCandidate, ActionRisk, sanitized_request
    candidates = []
    for item in sanitize_controls(snapshot_actionable_controls(page)):
        try:
            candidate = ActionCandidate(str(item["id"]), str(item["role"]), str(item["name"]))
        except (KeyError, ValueError):
            continue
        if candidate.actionable:
            candidates.append(candidate)
    request = sanitized_request(candidates, site_id=portal, intent=intent)
    if not request.candidates:
        return None
    trace = tuple(provider.recover(request))
    if len(trace) != 1:
        return None
    action = trace[0]
    by_id = {item.candidate_id: item for item in request.candidates}
    candidate = by_id.get(action.candidate_id)
    if candidate is None or candidate.role != action.target_role or action.risk_for(candidate) is not ActionRisk.LOW:
        return None
    return {"candidate_id": candidate.candidate_id, "role": candidate.role, "intent": intent}


def record_recovery_shadow_task(portal: str, proposal: dict) -> str | None:
    import sqlite3
    import uuid
    from datetime import datetime, timezone
    candidate_id = str(proposal.get("candidate_id") or "")
    intent = str(proposal.get("intent") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", candidate_id) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", intent):
        return None
    summary = f"{portal} recovery shadow proposed {candidate_id} for {intent}"
    path = os.environ.get("JOBHUNT_CONTROL_DB", "/var/lib/jobhunt/controlplane.db")
    try:
        with sqlite3.connect(path, timeout=5) as db:
            row = db.execute("SELECT id FROM operator_tasks WHERE status='open' AND type='recipe_drift' AND safe_summary=? LIMIT 1", (summary,)).fetchone()
            if row:
                return str(row[0])
            task_id = uuid.uuid4().hex
            db.execute("INSERT INTO operator_tasks (id,run_id,site_id,type,status,safe_summary,created_at) VALUES(?,NULL,NULL,'recipe_drift','open',?,?)", (task_id, summary, datetime.now(timezone.utc).isoformat()))
            return task_id
    except (OSError, sqlite3.Error):
        return None
