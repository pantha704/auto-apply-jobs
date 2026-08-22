#!/usr/bin/env python3
"""Wellfound apply worker. Claims wellfound jobs from queue, applies via dialog flow.
Usage: python3 worker_wellfound.py <worker_id>
"""
import json, os, re, sqlite3, sys, time, signal
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright
import audit
import dynamic_ui
from workflow.worker_telemetry import telemetry_for
import jd_match
from workflow.browser_use_client import BrowserUseSidecar
from submission_signals import has_submission_confirmation
from title_filter import is_tech_title, title_rejection_reason
from worker_guard import BrowserWatchdog, exit_if_fired
import profile as ident

GUARD = None  # active per-job BrowserWatchdog (set inside apply_one)

HERE = os.path.dirname(os.path.abspath(__file__))
CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
STATE = os.path.join(HERE, "portal_wellfound.json")
RESUME = "/home/ubuntu/Documents/Pratham_Jaiswal_Updated_Resume.pdf"
WORKER_ID = sys.argv[1] if len(sys.argv) > 1 else "wf-w1"
QUEUE_DB = os.getenv("JOBHUNT_QUEUE_DB", os.path.join(HERE, "apply_queue.db"))
STATE_ROOT = os.getenv("JOBHUNT_STATE_ROOT", os.path.join(HERE, "state_queue"))
PASSWORD = os.environ.get("WF_PASSWORD", "")
SALARY_USD = os.environ.get("WF_SALARY_USD", "85000")
RECOVERY_MODE = os.environ.get("JOBHUNT_RECOVERY_MODE", "disabled").lower()
_worker_match = re.search(r"(\d+)$", WORKER_ID)
_worker_number = int(_worker_match.group(1)) if _worker_match else 1
CDP_PORT = int(os.environ.get("WF_CDP_PORT", str(9330 + _worker_number)))
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"

# Real apply modal only — excludes TrustArc consent banner (also role=dialog)
APPLY_DIALOG = "[role=dialog][aria-modal=true]:not([id*=truste]):not([id*=consent]), .ReactModal__Content"

ANSWERS = {}
SNAPS = {}

PROFILE = {"name": ident.NAME, "email": ident.EMAIL,
           "location": f"{ident.CITY}, India" if ident.CITY else "", "years": "1", "salary": SALARY_USD,
           "note": "Full-stack & Solana engineer (Turbin3 cohort; 4 merged OSS PRs). Built AI crawler, DeFi credit scoring, RWA tokenization platform."}


def safe_diagnostic_text(text):
    """Remove known candidate values before persisting browser diagnostics."""
    safe = text or ""
    values = [PROFILE.get("name"), PROFILE.get("email"), PROFILE.get("location")]
    for value in values:
        if value and len(str(value)) >= 3:
            safe = re.sub(re.escape(str(value)), "[REDACTED]", safe, flags=re.I)
    return safe

def db():
    return sqlite3.connect(QUEUE_DB)

def telemetry():
    return telemetry_for(WORKER_ID, "wellfound", QUEUE_DB, STATE_ROOT)

def claim():
    while True:
        c = db()
        row = c.execute("SELECT id, url, title FROM jobs WHERE portal='wellfound' AND status='pending' ORDER BY prio DESC, rowid LIMIT 1").fetchone()
        if not row:
            c.close(); telemetry().idle(); return None
        reason = title_rejection_reason(row[2] or "", "wellfound")
        if reason:
            c.execute("UPDATE jobs SET status='skip', result=? WHERE id=? AND status='pending'", (reason, row[0]))
            c.commit(); c.close()
            telemetry().outcome(row[0], "skip", reason)
            continue
        upd = c.execute("UPDATE jobs SET status='claimed', claimed_by=? WHERE id=? AND status='pending'", (WORKER_ID, row[0]))
        c.commit(); c.close()
        if upd.rowcount == 1:
            telemetry().claimed(row[0])
            return {"id": row[0], "url": row[1], "title": row[2]}

def mark(jid, status, result=""):
    c = db()
    c.execute("UPDATE jobs SET status=?, result=? WHERE id=?", (status, result[:200], jid))
    c.commit(); c.close()
    telemetry().outcome(jid, status, result)

def fill(page, label, value):
    try:
        el = page.locator(f"input[aria-label*='{label}'], textarea[aria-label*='{label}'], input[placeholder*='{label}']").first
        el.fill(value)
        return True
    except Exception:
        return False

def fill_dlg(page, label, value):
    try:
        dlg = page.locator(APPLY_DIALOG)
        el = dlg.locator(f"input[aria-label*='{label}'], textarea[aria-label*='{label}'], input[placeholder*='{label}']").first
        el.fill(value)
        return True
    except Exception:
        return False

def expand_category(url, ctx, page):
    """Category page (/role/r/...): scrape individual /jobs/ links and enqueue them.
    Returns (count_added, first_job_url) or (0, None)."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=40000)
        page.wait_for_timeout(5000)
        try:
            consent = page.locator("button:has-text('Reject All'), button:has-text('Agree & Proceed')")
            if consent.count() > 0:
                consent.first.click(timeout=3000)
                page.wait_for_timeout(1500)
        except Exception:
            pass
        links = page.evaluate("""() => [...document.querySelectorAll('a[href*="/jobs/"]')].map(a => a.href)""")
        links = [l for l in links if re.match(r"https?://wellfound\.com/jobs/\d+", l)]
        links = list(dict.fromkeys(links))  # dedupe preserve order
        if not links:
            return 0, None
        c = db()
        added = 0
        for l in links[:60]:  # cap per category page
            jid = "wf-" + l.split("/jobs/")[-1].split("-")[0]
            try:
                cur = c.execute("SELECT id FROM jobs WHERE id=? OR url=?", (jid, l)).fetchone()
                if cur:
                    continue
                ttl = l.split("/jobs/")[-1].split("-", 1)[-1].replace("-", " ")[:80]
                if not is_tech_title(ttl, "wellfound"):
                    continue
                c.execute("INSERT OR IGNORE INTO jobs (id, portal, url, title, source, status, prio, fetched_at) VALUES (?,?,?,?,?,?,?,?)",
                          (jid, "wellfound", l, ttl, "wellfound", "pending", 5, time.strftime("%Y-%m-%dT%H:%M:%SZ")))
                added += 1
            except Exception:
                pass
        c.commit()
        c.close()
        return added, (links[0] if added else None)
    except Exception as e:
        return 0, None

def dismiss_consent(page):
    """Dismiss the TrustArc cookie banner if present (in-page overlay that
    otherwise eats submit clicks). Waits briefly for it to render (the old
    handler fired at goto(commit) before the banner existed). Idempotent:
    returns instantly when absent. Consent choice persists in the worker
    profile, so this only fires once per profile lifetime in practice."""
    try:
        try:
            page.wait_for_selector("#truste-consent-button, #truste-consent-required, #truste-consent-close",
                                   state="visible", timeout=4000)
        except Exception:
            return False
        for sel in ["#truste-consent-button", "#truste-consent-required", "#truste-consent-close"]:
            el = page.query_selector(sel)
            if el and el.is_visible():
                try:
                    el.click(timeout=3000)
                except Exception:
                    pass
                page.wait_for_timeout(1500)
                return True
    except Exception:
        pass
    return False


def recovery_shadow(page, intent):
    """Analyze pre-fill UI drift without allowing Browser Use to mutate the page."""
    if RECOVERY_MODE != "shadow":
        return None
    try:
        provider = BrowserUseSidecar(CDP_URL)
        proposal = dynamic_ui.browser_use_shadow_analysis(
            page, "wellfound", intent, provider
        )
        if proposal:
            dynamic_ui.record_recovery_shadow_task("wellfound", proposal)
            print(
                f"[{WORKER_ID}] recovery-shadow proposal {proposal['candidate_id']} for {intent}",
                flush=True,
            )
        return proposal
    except Exception as exc:
        print(
            f"[{WORKER_ID}] recovery-shadow unavailable: {type(exc).__name__}",
            flush=True,
        )
        return None


def apply_dialog_open(page) -> bool:
    """Wait for the verified navigation postcondition after an Apply click."""
    try:
        page.locator(APPLY_DIALOG).first.wait_for(state="visible", timeout=6000)
        return True
    except Exception:
        return False


def apply_one(url, jid=None):
    global GUARD
    for attempt in range(2):
        GUARD = BrowserWatchdog(f"wf_w_{WORKER_ID}", max_sec=190, job=(jid, url))
        GUARD.start()
        try:
            return _apply_one(url)
        except Exception as e:
            if GUARD.fired.is_set():
                GUARD.stop()
                raise  # browser was SIGKILLed — bubble up so main() exits
            GUARD.stop()
            if attempt == 1:
                return (False, str(e)[:120])
            time.sleep(3)
        finally:
            GUARD.stop()
    return (False, "exhausted-retries")

def _apply_one(url):
    global ANSWERS, SNAPS
    ANSWERS = {"name": PROFILE["name"], "email": PROFILE["email"],
               "location": PROFILE["location"], "years": PROFILE["years"],
               "salary": PROFILE["salary"], "auth": "No"}
    SNAPS = {}
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=os.path.join(HERE, "profiles", f"wf_w_{WORKER_ID}"), executable_path=CLOAK, headless=True,
            args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
                  "--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={CDP_PORT}",
                  "--window-size=1400,900"])
        if os.path.exists(STATE):
            try: ctx.add_cookies(json.load(open(STATE)).get("cookies", []))
            except Exception: pass
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(15000)
        page.set_default_navigation_timeout(20000)
        try:
            page.goto(url, wait_until="commit", timeout=45000)
            # CATEGORY PAGE (/role/r/...) — expand to individual jobs, not an apply target
            if "/role/r/" in page.url or "/role/r/" in url:
                added, first = expand_category(url, ctx, page)
                if first:
                    page.goto(first, wait_until="commit", timeout=45000)
                else:
                    return (False, "category-no-jobs")
            # dismiss cookie consent banner (TrustArc) — wait for render, then click
            dismiss_consent(page)
            # double-apply guard: if this URL was already SUBMITTED in the audit
            # table, never re-apply (prevents reviewer/retry double-submissions).
            try:
                import hashlib as _hl
                uh = _hl.sha1(audit.canonical(url).encode()).hexdigest()
                prev = audit.db().execute(
                    "SELECT status, applied_at FROM applications WHERE portal='wellfound' AND url_hash=?",
                    (uh,)).fetchone()
                if prev and prev[0] == "submitted":
                    return (False, "already-applied")
            except Exception:
                pass
            try:
                page.get_by_role("button", name=re.compile("^Apply Now$", re.I)).first.wait_for(state="visible", timeout=8000)
            except Exception:
                pass
            # intent map first; no raw-selector fallback
            dialog_open = False
            for attempt in range(3):
                try:
                    if not dynamic_ui.hybrid_click(
                        page,
                        "wellfound",
                        "apply",
                        CDP_URL,
                        postcondition=lambda: apply_dialog_open(page),
                    ):
                        break
                    try:
                        page.locator(APPLY_DIALOG).first.wait_for(state="visible", timeout=6000)
                    except Exception:
                        pass
                except Exception:
                    break
                if page.locator(APPLY_DIALOG).count() > 0:
                    try:
                        txt = page.locator(APPLY_DIALOG).inner_text().lower()
                    except Exception:
                        txt = ""
                    if "we value your privacy" in txt or "trustarc" in txt:
                        # consent banner faked the dialog match — dismiss and retry
                        try:
                            dynamic_ui.click(page, "wellfound", "dismiss_consent", timeout_ms=3000)
                            page.wait_for_timeout(1500)
                        except Exception:
                            pass
                        dialog_open = False
                        continue
                    dialog_open = True
                    break
            if not dialog_open:
                # fast external-apply skip: no Apply Now + external apply signal -> skip, no autoOpen
                try:
                    txt = page.inner_text("body")[:800]
                    ext = "Application: External" in txt or "External" in txt or "redirect" in txt.lower()
                except Exception:
                    ext = False
                if ext:
                    return (False, "external-apply")
                try:
                    page.goto(url + ("&" if "?" in url else "?") + "autoOpenApplication=true", wait_until="commit", timeout=30000)
                    try:
                        page.locator("[role=dialog]").first.wait_for(state="visible", timeout=6000)
                        dialog_open = True
                    except Exception:
                        dialog_open = False
                except Exception:
                    pass
            if not dialog_open:
                recovery_shadow(page, "open_apply_dialog")
                return (False, "no-dialog")
            # EXTERNAL-MANAGED dialog: "Apply on website" — no form to fill, not a failure
            try:
                dlg_txt = page.locator(APPLY_DIALOG).inner_text()[:500].lower()
                if "managed outside of wellfound" in dlg_txt or "apply on website" in dlg_txt or "apply on the company website" in dlg_txt:
                    return (False, "external-apply")
            except Exception:
                pass
            # SESSION-EXPIRED dialog: signup form shown (Set a Password*) or "existing user, log in"
            # → click Log In, let profile Google SSO re-auth, then wait for dialog to become the logged-in form
            try:
                dlg_txt2 = page.locator(APPLY_DIALOG).inner_text()[:600].lower()
                if ("set a password" in dlg_txt2 or "existing user" in dlg_txt2) and "log in" in dlg_txt2:
                    logged_in = False
                    for attempt in range(2):
                        before_login_url = page.url
                        if not dynamic_ui.hybrid_click(
                            page, "wellfound", "login", CDP_URL,
                            postcondition=lambda: page.url != before_login_url,
                        ):
                            continue
                        # wait for navigation through google SSO and back
                        try:
                            page.wait_for_load_state("domcontentloaded", timeout=20000)
                        except Exception:
                            pass
                        page.wait_for_timeout(4000)
                        # back on job page? dialog may have closed — reopen via Apply Now / autoOpen
                        dynamic_ui.hybrid_click(
                            page, "wellfound", "apply", CDP_URL,
                            postcondition=lambda: page.locator(APPLY_DIALOG).first.is_visible(),
                        )
                        try:
                            page.locator(APPLY_DIALOG).first.wait_for(state="visible", timeout=8000)
                        except Exception:
                            pass
                        try:
                            t3 = page.locator(APPLY_DIALOG).inner_text()[:500].lower()
                        except Exception:
                            t3 = ""
                        if "set a password" not in t3 and "existing user" not in t3 and t3:
                            logged_in = True
                            break
                    if not logged_in:
                        return (False, "session-expired")
            except Exception:
                pass
            # JD-aware honest match (deterministic, zero-cost)
            try:
                jd_text = page.inner_text("body")[:6000]
            except Exception:
                jd_text = ""
            match = jd_match.analyze(jd_text)
            ANSWERS["jd_matched"] = match.get("matched", [])
            ANSWERS["jd_gaps"] = match.get("gaps", [])
            ANSWERS["jd_senior"] = bool(match.get("senior"))
            if match["decision"] == "skip":
                return (False, "jd-" + match["reason"])
            # fill dialog (placeholder-based — wellfound uses placeholders, not labels)
            dlg = page.locator(APPLY_DIALOG)
            def ph(p, v):
                try:
                    dlg.locator(f"input[placeholder*='{p}'], textarea[placeholder*='{p}']").first.fill(v)
                    return True
                except Exception:
                    return False
            ph("Jane Doe", PROFILE["name"])
            ph("mail@website.com", PROFILE["email"])
            if PASSWORD:
                pw = dlg.locator("input[type=password]")
                if pw.count() >= 2:
                    pw.nth(0).fill(PASSWORD)
                    pw.nth(1).fill(PASSWORD)
            ph("e.g. San Francisco", PROFILE["location"])
            # open to work remotely checkbox
            try:
                cb = page.locator(APPLY_DIALOG + " input[type=checkbox]").first
                if not cb.is_checked(): cb.click()
            except Exception: pass
            # years of experience select
            try:
                sel = page.locator(APPLY_DIALOG + " select").first
                sel.select_option(label="1 year")
            except Exception:
                pass
            # years of experience — custom downshift combobox fallback (no native select)
            try:
                if page.locator(APPLY_DIALOG + " select").count() == 0:
                    combo = page.locator(APPLY_DIALOG + " [role=combobox]").first
                    combo_txt = combo.inner_text().lower() if combo.count() else ""
                    if combo.count() and ("select" in combo_txt or not combo_txt.strip()):
                        combo.click(timeout=4000)
                        page.wait_for_timeout(1200)
                        # prefer an exact "1 year" option; else the first numeric option
                        opt = page.locator("[role=option]:has-text('1 year'), [role=option]:has-text('1 Year')").first
                        if opt.count() == 0:
                            opt = page.locator("[role=option]").first
                        if opt.count() and opt.is_visible():
                            opt.click(timeout=4000)
            except Exception:
                pass
            # desired salary
            try:
                num = page.locator(APPLY_DIALOG + " input[type=number]").first
                num.fill(PROFILE["salary"])
            except Exception:
                pass
            # work authorization radios: answer honestly per question text, never guess
            try:
                for r in page.locator(APPLY_DIALOG + " [role=radio]").all():
                    label = (r.evaluate("el => el.getAttribute('aria-label') || ''") or "").strip().lower()
                    if label != "no":
                        continue
                    try:
                        q = r.evaluate("""el => {
                            const f = el.closest('fieldset, [role=group], div');
                            if (!f) return '';
                            const leg = f.querySelector('legend');
                            return (leg ? leg.innerText : f.innerText || '').slice(0, 200);
                        }""")
                    except Exception:
                        q = ""
                    q = (q or "").lower()
                    if "sponsor" in q:
                        # honest answer to "require sponsorship?" is YES
                        try:
                            g = r.locator("xpath=ancestor::*[@role='group' or self::fieldset][1]")
                            yes = g.locator("[role=radio][aria-label='Yes' i]").first
                            if yes.count():
                                yes.click()
                        except Exception:
                            pass
                        continue
                    if "authoriz" in q:
                        r.click()
                    # unknown question -> leave untouched
            except Exception:
                pass
            # cover letter textarea — JD-tailored note, fallback to static profile note.
            # Fill ALL visible empty textareas (some dialogs add custom questions
            # like "What interests you about this company?" as extra textareas —
            # leaving them empty blocks submission).
            try:
                note_txt = (match.get("note") or PROFILE.get("note") or "").strip()
                if note_txt:
                    for ta in page.locator(APPLY_DIALOG + " textarea:visible").all():
                        try:
                            if not ta.input_value().strip():
                                ta.fill(note_txt)
                        except Exception:
                            pass
            except Exception:
                pass
            # custom-question text inputs (customQuestionAnswers[N][answer]) — fill if empty
            try:
                for ci in page.locator(APPLY_DIALOG + " input:visible").all():
                    try:
                        nm = ci.evaluate("el => (el.getAttribute('name')||'') + '|' + (el.getAttribute('placeholder')||'')")
                        if ("customQuestion" in nm or "answer" in nm.lower()) and not ci.input_value().strip():
                            ci.fill((match.get("note") or PROFILE.get("note") or "").strip()[:250])
                    except Exception:
                        pass
            except Exception:
                pass
            ANSWERS["note"] = note_txt
            # resume upload via file input
            try:
                page.locator(APPLY_DIALOG + " input[type=file]").first.set_input_files(RESUME)
                ANSWERS["resume"] = os.path.basename(RESUME)
                page.wait_for_timeout(2000)
            except Exception:
                pass
            # audit snapshot BEFORE submit
            try:
                SNAPS["before"] = audit.snapshot(page, "wellfound", url, "before")
            except Exception:
                pass
            # pre-submit block detection (location/timezone constraints etc.)
            try:
                dlg_text = page.locator(APPLY_DIALOG).inner_text()[:600].lower()
            except Exception:
                dlg_text = ""
            if "not accepting applications" in dlg_text or "timezone or relocation" in dlg_text or "does not support the locations" in dlg_text:
                return (False, "location-block")
            if "does not offer visa sponsorship" in dlg_text or ("requires sponsorship" in dlg_text and "in-country" in dlg_text):
                return (False, "sponsorship-block")
            # re-dismiss consent banner in case it re-rendered over the dialog
            dismiss_consent(page)
            page.wait_for_timeout(800)
            # debug dump: pre-submit field state (cheap, always on)
            try:
                with open(os.path.join(HERE, "logs", "wf_submit_fail.log"), "a") as df:
                    df.write(f"\n===== PRE-SUBMIT {time.strftime('%H:%M:%S')} {url} =====\n")
                    for f in page.locator(APPLY_DIALOG + " input, " + APPLY_DIALOG + " textarea, " + APPLY_DIALOG + " select").all():
                        try:
                            field_id = f.evaluate("el => el.name || el.type || el.tagName")
                            df.write(f"[field] {field_id} filled={bool(f.input_value())}\n")
                        except Exception:
                            pass
                    df.write(f"[textareas visible: {page.locator(APPLY_DIALOG + ' textarea:visible').count()}]\n")
            except Exception:
                pass
            # Capture only application-scoped mutation responses, and install
            # the listener BEFORE clicking submit so the request cannot race us.
            submit_http = []
            try:
                def _on_resp(r):
                    try:
                        if r.request.method in ("POST", "PUT") and re.search(r"application|apply|send|candidate", r.url):
                            submit_http.append((r.status, r.url[:90]))
                    except Exception:
                        pass
                page.on("response", _on_resp)
            except Exception:
                pass
            # submit
            try:
                btn = page.locator(APPLY_DIALOG + " button:has-text('Submit application')")
                if btn.count() == 0:
                    btn = page.locator(APPLY_DIALOG + " button:has-text('Send application')")
                if btn.count() == 0:
                    btn = page.locator(APPLY_DIALOG + " button:has-text('Submit')")
                if os.environ.get("WF_DEBUG"):
                    try:
                        with open(os.path.join(HERE, "logs", "wf_submit_fail.log"), "a") as df:
                            _dattr = btn.first.evaluate("el => el.getAttribute('disabled') || ''") if btn.count() else ''
                            df.write(f"\n[submit] buttons found={btn.count()} disabled={btn.first.is_disabled() if btn.count() else 'n/a'} html={_dattr}\n")
                            df.write(f"[submit] comboboxes: {page.locator(APPLY_DIALOG + ' [role=combobox]').count()}\n")
                            for cb in page.locator(APPLY_DIALOG + " [role=combobox]").all():
                                try:
                                    _aexp = cb.evaluate("el => el.getAttribute('aria-expanded') || ''")
                                    df.write(f"[combobox] txt='{cb.inner_text()[:40]}' aria={_aexp}\n")
                                except Exception:
                                    pass
                    except Exception:
                        pass
                if not dynamic_ui.click(page, "wellfound", "submit", timeout_ms=5000):
                    raise RuntimeError("submit intent failed")
                if os.environ.get("WF_DEBUG"):
                    try:
                        with open(os.path.join(HERE, "logs", "wf_submit_fail.log"), "a") as df:
                            df.write(f"[submit] click OK at {time.strftime('%H:%M:%S')}\n")
                    except Exception:
                        pass
            except Exception:
                try:
                    diag = page.locator(APPLY_DIALOG).inner_text()[:180].replace("\n", " | ")
                    if not diag.strip():
                        diag = page.inner_text("body")[:180].replace("\n", " | ")
                except Exception:
                    diag = page.inner_text("body")[:180].replace("\n", " | ") if True else "?"
                return (False, "no-submit-btn|" + diag)
            # success = POSITIVE signal only: relevant HTTP 2xx or explicit
            # confirmation text. Dialog closure alone is never proof.
            success = False
            try:
                for _ in range(12):
                    page.wait_for_timeout(1000)
                    if has_submission_confirmation(
                        http_statuses=[st for st, _ in submit_http]
                    ):
                        success = True
                        break
                    try:
                        dtxt = page.locator(APPLY_DIALOG).inner_text() if page.locator(APPLY_DIALOG).count() else ""
                        if has_submission_confirmation(dtxt):
                            success = True
                            break
                    except Exception:
                        pass
                if not success:
                    success = has_submission_confirmation(page.inner_text("body"))
            except Exception:
                pass
            if success:
                try:
                    SNAPS["after"] = audit.snapshot(page, "wellfound", url, "after")
                except Exception:
                    pass
                return (True, "submitted")
            # not positively confirmed -> capture the actual dialog state as failure
            err = ""
            try:
                err = page.locator(APPLY_DIALOG).inner_text()[:300] if page.locator(APPLY_DIALOG).count() else page.inner_text("body")[:300]
            except Exception:
                err = ""
            # debug dump: full dialog state for post-mortem (cheap, always on)
            try:
                with open(os.path.join(HERE, "logs", "wf_submit_fail.log"), "a") as df:
                    df.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {url} =====\n")
                    diagnostic = page.locator(APPLY_DIALOG).inner_text() if page.locator(APPLY_DIALOG).count() else page.inner_text("body")
                    df.write(safe_diagnostic_text(diagnostic)[:3000])
                    df.write("\n")
                    for f in page.locator(APPLY_DIALOG + " input, " + APPLY_DIALOG + " textarea, " + APPLY_DIALOG + " select").all():
                        try:
                            field_id = f.evaluate("el => el.name || el.type || el.tagName")
                            df.write(f"[field] {field_id} filled={bool(f.input_value())}\n")
                        except Exception:
                            pass
            except Exception:
                pass
            if "not accepting applications" in err.lower() or "timezone or relocation" in err.lower():
                return (False, "location-block")
            return (False, "submit-unconfirmed")
        except Exception as e:
            return (False, str(e)[:120])
        finally:
            try: ctx.close()
            except Exception: pass

def main():
    def _alarm(signum, frame):
        raise TimeoutError("job hard timeout")
    signal.signal(signal.SIGALRM, _alarm)
    while True:
        job = claim()
        if not job:
            print(f"[{WORKER_ID}] queue empty, sleep", flush=True)
            time.sleep(60); continue
        print(f"[{WORKER_ID}] claim: {job['title'][:50]}", flush=True)
        signal.alarm(180)  # no job may hang the worker longer than 3 minutes
        try:
            ok, reason = apply_one(job["url"], job["id"])
        except Exception as e:
            ok, reason = False, f"hard-timeout|{str(e)[:60]}"
        finally:
            signal.alarm(0)
        if GUARD is not None and GUARD.fired.is_set():
            # Browser tree was SIGKILLed — the result is garbage. Requeue the
            # job (infra failure, not a real skip) and exit so systemd
            # restarts us with a clean browser.
            mark(job["id"], "pending", "browser-wedge-timeout")
            print(f"[{WORKER_ID}] BROWSER WEDGE — job requeued, exiting for systemd restart", flush=True)
            os._exit(7)
        mark(job["id"], "done" if ok else "skip", reason)
        try:
            audit.record_application(
                portal="wellfound", company="(wellfound)", role=job["title"], url=job["url"],
                status="submitted" if ok else f"skipped:{reason[:60]}",
                answers=ANSWERS, resume_used=os.path.basename(RESUME),
                note=reason[:200],
                snap_before=(SNAPS.get("before") or {}).get("png"),
                snap_after=(SNAPS.get("after") or {}).get("png"))
        except Exception as e:
            print(f"[{WORKER_ID}] audit record failed: {str(e)[:80]}", flush=True)
        print(f"[{WORKER_ID}] {'DONE' if ok else 'SKIP'}: {reason[:80]}", flush=True)
        time.sleep(3)

if __name__ == "__main__":
    main()
