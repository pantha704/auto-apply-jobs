#!/usr/bin/env python3
"""Autonomous LinkedIn Easy-Apply worker. Claims jobs from SQLite queue atomically.
Usage: python3 worker_linkedin.py <worker_id>
"""
import json, os, re, shutil, sqlite3, sys, time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
import audit
import dynamic_ui
from workflow.worker_telemetry import telemetry_for
from workflow.portal_session_runtime import (
    PortalSessionUnavailable,
    current_session,
    inject_current_session,
)
from workflow.application_gate import (
    PublicationUnavailable,
    eligible_for_claim,
    pin_claim,
    published_runtime,
)
import jd_match
from title_filter import title_rejection_reason
from worker_guard import BrowserWatchdog

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.getenv("JOBHUNT_QUEUE_DB", os.path.join(HERE, "apply_queue.db"))
CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
RESUME = ""
WORKER_ID = sys.argv[1] if len(sys.argv) > 1 else "li-w1"
STATE_ROOT = os.getenv("JOBHUNT_STATE_ROOT", os.path.join(HERE, "state_queue"))
_worker_match = re.search(r"(\d+)$", WORKER_ID)
CDP_PORT = int(os.environ.get("LI_CDP_PORT", str(9370 + (int(_worker_match.group(1)) if _worker_match else 1))))
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
STEALTH = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

PROFILE_DATA = {}
REQUIRED_FACTS = (
    ("contact.phone", "profile.phone"),
    ("contact.address", "profile.address"),
    ("location.city", "profile.city"),
    ("location.state", "profile.state"),
    ("location.postal_code", "profile.pin"),
    ("urls.linkedin", "profile.linkedin"),
    ("urls.portfolio", "profile.portfolio"),
    ("education.college", "profile.college"),
    ("compensation.expected_inr", "profile.expected"),
    ("compensation.current_inr", "profile.current"),
    ("employment.notice_days", "profile.notice"),
    ("experience.years", "profile.years"),
    ("skills.stack", "profile.stack"),
    ("summary.pitch", "profile.pitch"),
)


def configure_publication(runtime):
    global PROFILE_DATA, RESUME
    PROFILE_DATA = {
        "phone": str(runtime.fact("contact.phone", "profile.phone")),
        "address": str(runtime.fact("contact.address", "profile.address")),
        "city": str(runtime.fact("location.city", "profile.city")),
        "state": str(runtime.fact("location.state", "profile.state")),
        "pin": str(runtime.fact("location.postal_code", "profile.pin")),
        "linkedin": str(runtime.fact("urls.linkedin", "profile.linkedin")),
        "portfolio": str(runtime.fact("urls.portfolio", "profile.portfolio")),
        "college": str(runtime.fact("education.college", "profile.college")),
        "expected": str(runtime.fact("compensation.expected_inr", "profile.expected")),
        "current": str(runtime.fact("compensation.current_inr", "profile.current")),
        "notice": str(runtime.fact("employment.notice_days", "profile.notice")),
        "years": str(runtime.fact("experience.years", "profile.years")),
        "stack": str(runtime.fact("skills.stack", "profile.stack")),
        "pitch": str(runtime.fact("summary.pitch", "profile.pitch")),
    }
    RESUME = runtime.resume_path
ZERO_TOPICS = ["go", "banking", "php", "laravel", "java", "excel", "wordpress", "django", "embedded", "iot", "excel"]
ONE_TOPICS = ["python", "typescript", "react", "node", "javascript", "database", "postgres", "sql",
              "full stack", "frontend", "backend", "software", "development", "information technology", "it "]

def db():
    return sqlite3.connect(DB)

def telemetry():
    return telemetry_for(WORKER_ID, "linkedin", DB, STATE_ROOT)

def claim(portal, runtime):
    while True:
        c = db()
        row = c.execute("SELECT id, url, title FROM jobs WHERE portal=? AND status='pending' ORDER BY prio DESC, rowid LIMIT 1", (portal,)).fetchone()
        if not row:
            c.close(); telemetry().idle(); return None
        reason = title_rejection_reason(row[2] or "", "linkedin")
        if reason:
            c.execute("UPDATE jobs SET status='skip', result=? WHERE id=? AND status='pending'", (reason, row[0]))
            c.commit(); c.close()
            telemetry().outcome(row[0], "skip", reason)
            continue
        if not eligible_for_claim(runtime, {"title": row[2] or "", "portal": portal}):
            c.execute("UPDATE jobs SET status='skip',result='policy-ineligible' WHERE id=? AND status='pending'", (row[0],))
            c.commit(); c.close()
            telemetry().outcome(row[0], "skip", "policy-ineligible")
            continue
        upd_count = pin_claim(c, row[0], WORKER_ID, runtime)
        c.commit(); c.close()
        if upd_count == 1:
            telemetry().claimed(row[0])
            return {"id": row[0], "url": row[1], "title": row[2]}

def mark(jid, status, result=""):
    c = db()
    c.execute("UPDATE jobs SET status=?, result=? WHERE id=?", (status, result[:200], jid))
    c.commit(); c.close()
    telemetry().outcome(jid, status, result)

def log(msg):
    print(f"[{WORKER_ID}] {msg}", flush=True)


def sync_playwright():
    """Load Playwright only when a LinkedIn browser job actually runs."""
    from playwright.sync_api import sync_playwright as _sync_playwright
    return _sync_playwright()


class LinkedInAuthRequired(Exception):
    """Saved LinkedIn session is missing, revoked, or challenge-gated."""

def auth_required(page):
    current = (page.url or "").lower()
    if any(x in current for x in ("/login", "/authwall", "/checkpoint", "/challenge", "/signup")):
        return True
    title = (page.title() or "").strip().lower()
    if title in {"sign up | linkedin", "sign in | linkedin"}:
        return True
    body = page.inner_text("body")[:5000].lower()
    return any(marker in body for marker in (
        "sign in to linkedin", "join linkedin", "verify your identity",
        "security verification", "new to linkedin? join now",
    ))

# ---------- JS helpers ----------
def js_text(page, q, value):
    return page.evaluate(f"""(() => {{
      const q = {json.dumps(q.lower())}; const V = {json.dumps(value)};
      let el = [...document.querySelectorAll('input,select,textarea')].find(e => (e.getAttribute('aria-label')||'').toLowerCase().includes(q));
      if (!el) {{ const lab = [...document.querySelectorAll('label')].find(e => (e.innerText||'').toLowerCase().includes(q)); if (lab && lab.htmlFor) el = document.getElementById(lab.htmlFor); }}
      if (!el) {{ const leaf = [...document.querySelectorAll('p,span')].find(e => e.children.length===0 && (e.innerText||'').toLowerCase().includes(q)); let p = leaf ? leaf.parentElement : null; for (let i=0;i<3 && p;i++) {{ const c = p.querySelector('input,select,textarea'); if (c) {{ el = c; break; }} p = p.parentElement; }} }}
      if (!el) return false;
      if (el.tagName === 'SELECT') {{
        const opt = [...el.options].find(o => (o.text||'').trim() === V);
        if (!opt) return false;
        el.value = opt.value; el.dispatchEvent(new Event('change', {{bubbles:true}})); return true;
      }}
      const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, V);
      el.dispatchEvent(new Event('input', {{bubbles:true}})); el.dispatchEvent(new Event('change', {{bubbles:true}}));
      return true;
    }})()""")

def js_radio(page, q, choice):
    return page.evaluate(f"""(() => {{
      const q = {json.dumps(q.lower())}; const choice = {json.dumps(choice.lower())};
      const leaves = [...document.querySelectorAll('p,span,label')].filter(e => e.children.length===0 && (e.innerText||'').toLowerCase().includes(q));
      if (!leaves.length) return false;
      leaves.sort((a,b) => (a.innerText||'').length - (b.innerText||'').length);
      const leaf = leaves[0];
      let p = leaf.parentElement; let scope = null;
      for (let i=0;i<6 && p;i++){{ if (p.querySelectorAll('[role=radio]').length) {{ scope = p; break; }} p = p.parentElement; }}
      if (!scope) return false;
      const r = [...scope.querySelectorAll('[role=radio]')].find(x => (x.getAttribute('aria-label')||'').trim().toLowerCase() === choice);
      if (!r) return false;
      r.click(); return true;
    }})()""")

def field_map(page):
    """Return dict label_lower -> element info for current page."""
    return page.evaluate("""() => {
      const out = {};
      for (const el of document.querySelectorAll('input,select,textarea')) {
        let lab = el.getAttribute('aria-label') || '';
        if (!lab && el.id) { const l = document.querySelector(`label[for="${el.id}"]`); if (l) lab = l.innerText; }
        if (!lab) { const wrap = el.closest('.artdeco-text-input--container, .fb-dash-form-element'); if (wrap) lab = (wrap.innerText||'').replace(el.value||'',''); }
        lab = (lab||'').trim().toLowerCase().slice(0,60);
        if (lab && lab !== 'search' && lab !== 'select language') {
          out[lab] = {type: el.type||'', tag: el.tagName, required: !!el.required, value: el.value||''};
        }
      }
      return out;
    }""")

def answer_page(page):
    """Auto-answer screening questions on current page. Returns count answered."""
    fields = field_map(page)
    n = 0
    for lab, f in fields.items():
        low = lab
        # selects with Yes/No
        if f["tag"] == "SELECT" and ("yes" in f["value"] or f["value"] == ""):
            opts = page.evaluate(f"""() => {{
              const lab = {json.dumps(lab)};
              const el = [...document.querySelectorAll('select')].find(e => ((e.getAttribute('aria-label')||'').toLowerCase().startsWith(lab) || e.id && (document.querySelector('label[for="'+e.id+'"]')?.innerText||'').toLowerCase().startsWith(lab)));
              return el ? [...el.options].map(o=>o.text.trim()) : [];
            }}""")
            if opts and "Yes" in opts and "No" in opts:
                # Unknown binary controls are intentionally left unanswered.
                choice = None
                if choice and js_text(page, lab, choice): n += 1
        if f["tag"] in ("INPUT", "TEXTAREA") and f["type"] in ("text", "tel", ""):
            if any(k in low for k in ["experience", "years", "salary", "ctc", "notice", "phone", "postal", "pin", "date", "start", "address", "city", "state", "gpa", "skills", "technolog", "framework", "unique", "url", "portfolio", "college", "university", "company", "title", "location", "lwd"]):
                val = None
                if "salary" in low or "ctc" in low:
                    val = PROFILE_DATA["expected"] if "expected" in low else PROFILE_DATA["current"]
                elif "notice" in low or "lwd" in low:
                    val = PROFILE_DATA["notice"]
                elif "phone" in low:
                    val = PROFILE_DATA["phone"]
                elif "address" in low: val = PROFILE_DATA["address"]
                elif "city" in low: val = PROFILE_DATA["city"]
                elif "state" in low: val = PROFILE_DATA["state"]
                elif "postal" in low or "pin" in low: val = PROFILE_DATA["pin"]
                elif "linkedin" in low: val = PROFILE_DATA["linkedin"]
                elif "portfolio" in low or "website" in low: val = PROFILE_DATA["portfolio"]
                elif "college" in low or "university" in low: val = PROFILE_DATA["college"]
                elif "technolog" in low: val = PROFILE_DATA["stack"]
                elif "framework" in low: val = "Next.js, React, Node.js, Hono"
                elif "unique" in low: val = PROFILE_DATA["pitch"]
                elif "start" in low: val = "Immediate"
                elif "skills" in low: val = "4"
                elif "gpa" in low: val = ""  # skip GPA
                elif "experience" in low or "years" in low:
                    if any(k in low for k in ZERO_TOPICS): val = "0"
                    elif any(k in low for k in ONE_TOPICS): val = PROFILE_DATA["years"]
                    else: val = None
                if val is not None and f["value"] == "":
                    if js_text(page, lab, val): n += 1
    return n

def main():
    while True:
        try:
            runtime = published_runtime("linkedin", REQUIRED_FACTS)
            configure_publication(runtime)
            session_snapshot = current_session("linkedin")
        except PublicationUnavailable:
            log("PUBLISHED PROFILE/POLICY NOT READY — waiting before claim")
            time.sleep(300)
            continue
        except PortalSessionUnavailable:
            log("LINKEDIN SESSION NOT VALID — stopping before claim")
            os._exit(12)
        job = claim("linkedin", runtime)
        if not job:
            log("queue empty, sleeping")
            time.sleep(60)
            continue
        log(f"claim: {job['title'][:60]}")
        GUARD = BrowserWatchdog(f"li_w_{WORKER_ID}", max_sec=240, job=(job["id"], job["url"]))
        GUARD.start()
        try:
            ok = apply_job(job["url"], session_snapshot.revision)
        except LinkedInAuthRequired:
            mark(job["id"], "pending", "linkedin-session-required")
            log("LINKEDIN SESSION REQUIRED — job requeued; stopping for session renewal")
            GUARD.stop()
            os._exit(12)
        except Exception as e:
            if GUARD.fired.is_set():
                mark(job["id"], "pending", "browser-wedge-timeout")
                log("BROWSER WEDGE — job requeued, exiting for systemd restart")
                os._exit(7)
            mark(job["id"], "error", str(e)[:150])
            log(f"ERROR: {str(e)[:120]}")
            GUARD.stop(); time.sleep(2); continue
        GUARD.stop()
        if GUARD.fired.is_set():
            mark(job["id"], "pending", "browser-wedge-timeout")
            log("BROWSER WEDGE — job requeued, exiting for systemd restart")
            os._exit(7)
        mark(job["id"], "done" if ok[0] else "skip", ok[1])
        try:
            audit.record_application(
                portal="linkedin", company=job.get("company") or "(linkedin)", role=job["title"],
                url=job["url"], status="submitted" if ok[0] else f"skipped:{ok[1][:60]}",
                answers=getattr(apply_job, "last_answers", None),
                resume_used=os.path.basename(RESUME), note=ok[1][:200])
            log(f"{'DONE' if ok[0] else 'SKIP'}: {job['title'][:50]} ({ok[1][:60]})")
        except Exception as e:
            mark(job["id"], "error", str(e)[:150])
            log(f"ERROR: {str(e)[:120]}")
        time.sleep(2)

def apply_job(url, expected_session_revision=None):
    apply_job.last_answers = {"phone": PROFILE_DATA["phone"], "address": PROFILE_DATA["address"],
                              "city": PROFILE_DATA["city"], "state": PROFILE_DATA["state"],
                              "pin": PROFILE_DATA["pin"], "college": PROFILE_DATA["college"],
                              "linkedin": PROFILE_DATA["linkedin"], "portfolio": PROFILE_DATA["portfolio"]}
    with sync_playwright() as p:
        # Never reuse a possibly wedged persistent context. The pinned canonical
        # revision is injected below, so every job gets a clean runtime profile.
        profile_dir = f"/tmp/li_w_{WORKER_ID}_{os.getpid()}"
        shutil.rmtree(profile_dir, ignore_errors=True)
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir, executable_path=CLOAK, headless=True,
            args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
                  "--window-size=1400,900", "--remote-debugging-address=127.0.0.1",
                  f"--remote-debugging-port={CDP_PORT}"])
        try:
            session_revision = inject_current_session(
                ctx, "linkedin", expected_revision=expected_session_revision
            )
            log(f"session revision {session_revision} pinned")
        except PortalSessionUnavailable as exc:
            ctx.close()
            raise LinkedInAuthRequired("linkedin-session-required") from exc
        ctx.add_init_script(STEALTH)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            for gtry in range(3):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    break
                except Exception:
                    if gtry == 2:
                        return (False, "goto-fail")
                    page.wait_for_timeout(4000)
            page.wait_for_timeout(2500)
            if auth_required(page):
                raise LinkedInAuthRequired("linkedin-session-required")
            txt = page.inner_text("body")
            eligibility = jd_match.analyze(txt[:6000], approved_skills=PROFILE_DATA["stack"], approved_years=PROFILE_DATA["years"], blockers=False)
            if eligibility["decision"] == "skip":
                return (False, "jd-" + eligibility["reason"])
            try:
                page.locator("button:has-text('Easy Apply')").first.wait_for(state="visible", timeout=12000)
                is_easy = True
            except Exception:
                is_easy = False
            if not is_easy:
                if "Responses managed off LinkedIn" in txt or "No longer accepting" in txt or "no longer accepting" in txt:
                    return (False, "external-or-closed")
                return (False, "no-easy-apply")

            try:
                if not dynamic_ui.hybrid_click(
                    page, "linkedin", "easy_apply", CDP_URL,
                    postcondition=lambda: page.locator("[role=dialog], .jobs-easy-apply-modal").count() > 0,
                ):
                    return (False, "no-easy-apply")
                page.wait_for_timeout(2000)
            except Exception:
                return (False, "easy-apply-intent-failed")
            modal_open = "Apply to" in page.inner_text("body")[:600] or "Contact info" in page.inner_text("body")[:600]
            # single-page form detection: Submit application present, no Next
            has_submit = page.locator("button:has-text('Submit application')").count() > 0
            has_next = page.locator("button:has-text('Next')").count() > 0
            if has_submit and not has_next:
                # ensure resume selected
                page.evaluate("""() => {
                  const rs=[...document.querySelectorAll('input[type=radio]')];
                  if (rs.some(r=>r.checked)) return 'ok';
                  const t=rs.find(r=>{let p=r.parentElement;for(let i=0;i<5&&p;i++){if((p.innerText||'').includes('Pratham_Jaiswal_Updated_Resume'))return true;p=p.parentElement;}return false;});
                  if (t) t.click(); else if (rs.length) rs[0].click();
                  return 'selected';
                }""")
                page.wait_for_timeout(800)
                try:
                    audit.snapshot(page, "linkedin", url, "before")
                except Exception:
                    pass
                if not dynamic_ui.click(page, "linkedin", "submit", timeout_ms=8000):
                    return (False, "submit-intent-failed")
                page.wait_for_timeout(3500)
                try:
                    audit.snapshot(page, "linkedin", url, "after")
                except Exception:
                    pass
                body = page.inner_text("body")
                ok = bool(re.search(r"Application submitted|Your application was sent", body, re.I))
                return (ok, "single-page-submitted" if ok else "single-page-uncertain|" + body[:120].replace("\n", " | "))
            # multi-page: fill phone + address, then advance
            # page 1: contact — fill phone if empty
            try:
                for ph in page.query_selector_all("input[type=tel]"):
                    if not ph.input_value():
                        ph.fill(PROFILE_DATA["phone"])
            except Exception:
                pass
            # address/city/state/postal if present and empty
            for lab in ["Address", "City", "State", "Postal", "Postal Code"]:
                try:
                    loc = page.locator(f"input[aria-label*='{lab}']")
                    if loc.count():
                        v = loc.first.input_value()
                        if not v:
                            val = {"Address": PROFILE_DATA["address"], "City": PROFILE_DATA["city"],
                                   "State": PROFILE_DATA["state"], "Postal": PROFILE_DATA["pin"], "Postal Code": PROFILE_DATA["pin"]}[lab]
                            loc.first.fill(val)
                except Exception:
                    pass
            if not advance(page, "Next", "contact"):
                return (False, "contact-next-unavailable")
            # resume page
            if not advance(page, "Next", "resume1"):
                return (False, "resume-next-unavailable")
            resume_ok = False
            for sel_attempt in range(4):
                page.evaluate("""() => {
                  const rs=[...document.querySelectorAll('input[type=radio]')];
                  if (rs.some(r=>r.checked)) return 'checked-ok';
                  const t=rs.find(r=>{let p=r.parentElement;for(let i=0;i<6&&p;i++){if((p.innerText||'').includes('Pratham_Jaiswal_Updated_Resume'))return true;p=p.parentElement;}return false;});
                  if (t) { t.click(); t.dispatchEvent(new Event('change',{bubbles:true})); const lab=t.closest('label'); if (lab) lab.click(); return 'clicked-ours'; }
                  if (rs.length) { rs[0].click(); return 'clicked-first'; }
                  return 'none';
                }""")
                page.wait_for_timeout(1300)
                en = page.evaluate("""() => {
                  const b=[...document.querySelectorAll('button')].find(x=>/^Next$/i.test((x.innerText||'').trim()));
                  return b ? {disabled: !!b.disabled} : {missing: true};
                }""")
                if not en.get("missing") and not en.get("disabled"):
                    resume_ok = True
                    break
            if not resume_ok:
                diag = page.evaluate("""() => [...document.querySelectorAll('button')].map(b=>((b.innerText||'').trim().slice(0,22)+':'+(b.disabled?1:0))).filter(x=>x.includes('Next')||x.includes('Review')).join(' | ') || 'no-next'""")
                return (False, "resume-stuck|" + diag[:100])
            if not hybrid_modal_click(page, ["next", "review"]):
                return (False, "resume-advance-unavailable")
            if "Resume*" in page.inner_text("body")[:400]:
                if not hybrid_modal_click(page, ["next", "review"]):
                    return (False, "resume-repeat-advance-unavailable")
            # work-experience page: add entry if required
            try:
                if page.get_by_role("button", name=re.compile("^Add work experience$", re.I)).count():
                    if not hybrid_modal_click(page, ["add_work_experience"]):
                        return (False, "add-work-experience-unavailable")
                    page.wait_for_timeout(1500)
                    for q, v in [("Your title", "Full-Stack Developer"), ("Company", "Braid-Forbes Health Research")]:
                        js_text(page, q, v)
                    page.evaluate("""() => {
                      const mod = document.querySelector('.jobs-easy-apply-modal, [role=dialog]') || document.body;
                      const sels = [...mod.querySelectorAll('select')];
                      if (sels[0]) { const o = [...sels[0].options].find(x=>x.text.trim()==='April'); if (o) { sels[0].value=o.value; sels[0].dispatchEvent(new Event('change',{bubbles:true})); } }
                      if (sels[1]) { const o = [...sels[1].options].find(x=>x.text.trim()==='2026'); if (o) { sels[1].value=o.value; sels[1].dispatchEvent(new Event('change',{bubbles:true})); } }
                      const cb = [...document.querySelectorAll('[role=checkbox]')].find(e => /currently work here/i.test(e.innerText||''));
                      if (cb && cb.getAttribute('aria-checked') !== 'true') cb.click();
                      return 'we-filled';
                    }""")
                    page.wait_for_timeout(800)
                    if not hybrid_modal_click(page, ["save"]):
                        return (False, "work-experience-save-unavailable")
                    page.wait_for_timeout(1500)
            except Exception:
                pass
            # screening pages — auto answer, up to 6 pages
            for pg in range(6):
                body = page.inner_text("body")
                if "Submit application" in body and "Review" not in body.split("Submit application")[0]:
                    pass
                ans = answer_page(page)
                if "Review" in page.inner_text("body"):
                    break
                # find next/review button
                if not hybrid_modal_click(page, ["review", "next"]):
                    return (False, "stuck-no-next")
                page.wait_for_timeout(1800)
            # review → verify errors → submit
            errs = page.evaluate("""() => { let n=0; for (const el of document.querySelectorAll('input,select,textarea')) { const hd=el.getAttribute('aria-describedby'); if (hd){ const h=document.getElementById(hd); if (h && (h.innerText||'').includes('required')) n++; } } return n; }""")
            if errs:
                # one more answer pass
                answer_page(page)
            if not click_terminal_submit(page):
                try:
                    diag = page.inner_text("body")[:250].replace("\n", " | ")
                except Exception:
                    diag = "?"
                return (False, "no-submit-btn|" + diag)
            try:
                audit.snapshot(page, "linkedin", url, "before")
            except Exception:
                pass
            page.wait_for_timeout(4000)
            try:
                audit.snapshot(page, "linkedin", url, "after")
            except Exception:
                pass
            body = page.inner_text("body")
            if re.search(r"Application submitted|Your application was sent", body, re.I):
                return (True, "submitted")
            return (False, "submit-uncertain|" + body[:150].replace("\n", " | "))
        except Exception as e:
            return (False, str(e)[:100])
        finally:
            try: ctx.close()
            except Exception: pass

def advance(page, label, note):
    return hybrid_modal_click(page, [label.lower()])


def _modal_signature(page):
    try:
        dialog = page.locator(".jobs-easy-apply-modal, [role=dialog]").first
        return dialog.inner_text()[:1200] if dialog.count() else page.url
    except Exception:
        return page.url


def hybrid_modal_click(page, intents):
    for intent in intents:
        before = _modal_signature(page)
        if dynamic_ui.hybrid_click(
            page, "linkedin", intent, CDP_URL,
            postcondition=lambda before=before: _modal_signature(page) != before,
        ):
            return True
    return False


def click_terminal_submit(page):
    for lab in ["^Submit application$"]:
        try:
            page.get_by_role("button", name=re.compile(lab, re.I)).first.click(timeout=3000)
            page.wait_for_timeout(1000)
            return True
        except Exception:
            continue
    return False

if __name__ == "__main__":
    main()
