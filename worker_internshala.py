#!/usr/bin/env python3
"""Autonomous Internshala worker — claims internships from the queue, opens the
detail page, applies with an honest cover note + resume upload, audits everything.
Usage: python3 worker_internshala.py <worker_id>
Pacing: DAILY_CAP applications/day (default 40), 45-90s between applies.
"""
import atexit, json, os, re, shutil, sqlite3, sys, tempfile, time

from jobhunt_time import ist_day_bounds
from title_filter import title_rejection_reason
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright
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
from submission_signals import has_submission_confirmation
from worker_guard import BrowserWatchdog

HERE = os.path.dirname(os.path.abspath(__file__))
CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
RESUME = ""
WORKER_ID = sys.argv[1] if len(sys.argv) > 1 else "is-w1"
QUEUE_DB = os.getenv("JOBHUNT_QUEUE_DB", os.path.join(HERE, "apply_queue.db"))
STATE_ROOT = os.getenv("JOBHUNT_STATE_ROOT", os.path.join(HERE, "state_queue"))
_worker_match = re.search(r"(\d+)$", WORKER_ID)
CDP_PORT = int(os.environ.get("IS_CDP_PORT", str(9350 + (int(_worker_match.group(1)) if _worker_match else 1))))
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
DAILY_CAP = int(os.environ.get("IS_DAILY_CAP", "40"))
STEALTH = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

PROFILE_DATA = {}
REQUIRED_FACTS = (
    ("location.remote_statement", "profile.remote_statement"),
    ("employment.availability_statement", "profile.availability_statement"),
    ("experience.years", "profile.years"),
    ("skills.stack", "profile.stack"),
    ("cover_note.default", "summary.pitch", "profile.pitch"),
)


def configure_publication(runtime):
    global PROFILE_DATA, RESUME
    PROFILE_DATA = {
        "remote_statement": str(runtime.fact("location.remote_statement", "profile.remote_statement")),
        "availability_statement": str(runtime.fact("employment.availability_statement", "profile.availability_statement")),
        "years": str(runtime.fact("experience.years", "profile.years")),
        "stack": str(runtime.fact("skills.stack", "profile.stack")),
        "pitch": str(runtime.fact("cover_note.default", "summary.pitch", "profile.pitch")),
    }
    RESUME = runtime.resume_path

def log(msg):
    print(f"[{WORKER_ID}] [{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def db():
    return sqlite3.connect(QUEUE_DB)

def telemetry():
    return telemetry_for(WORKER_ID, "internshala", QUEUE_DB, STATE_ROOT)

def claim(runtime):
    while True:
        c = db()
        row = c.execute("SELECT id, url, title FROM jobs WHERE portal='internshala' AND status='pending' ORDER BY prio DESC, rowid LIMIT 1").fetchone()
        if not row:
            c.close(); telemetry().idle(); return None
        reason = title_rejection_reason(row[2] or "", "internshala")
        if reason:
            c.execute("UPDATE jobs SET status='skip', result=? WHERE id=? AND status='pending'", (reason, row[0]))
            c.commit(); c.close()
            telemetry().outcome(row[0], "skip", reason)
            continue
        if not eligible_for_claim(runtime, {"title": row[2] or "", "portal": "internshala"}):
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

def applied_today():
    c = db()
    # Count TODAY's actual applications from the audit table (the source of truth).
    # Bug fixed 2026-08-17: we previously ALSO counted an ALL-TIME branch
    # (`jobs done AND result='applied' AND claimed_by`) and took max() of the two.
    # The all-time count (e.g. 40 from previous days) never reset overnight, so the
    # worker believed it had "hit today's cap 40" forever and slept 30m in a loop
    # while applying ZERO on the current day. Today's date partition is the only
    # correct cap signal.
    start_utc, end_utc = ist_day_bounds()
    n2 = c.execute("SELECT COUNT(*) FROM applications WHERE portal='internshala' AND applied_at >= ? AND applied_at < ? AND status='applied'", (start_utc, end_utc)).fetchone()[0]
    c.close()
    return n2
def fill_textareas(page, note):
    """Fill employer questions honestly. Returns count filled."""
    fields = page.evaluate("""() => {
      const out = [];
      for (const el of document.querySelectorAll('textarea')) {
        if (el.offsetParent === null) continue;
        let q = el.getAttribute('placeholder') || el.getAttribute('name') || '';
        if (!q) {
          const lab = el.closest('.form-group, .application_question') || el.parentElement;
          q = (lab ? lab.innerText : '').replace(el.value||'','').trim().slice(0,120);
        }
        out.push({q: q.slice(0,150), val: el.value||''});
      }
      return out;
    }""")
    n = 0
    for i, f in enumerate(fields):
        if f["val"].strip():
            continue
        q = f["q"].lower()
        if i == 0:
            text = note
        elif any(k in q for k in ["relocat", "location", "shift"]):
            text = PROFILE_DATA["remote_statement"]
        elif any(k in q for k in ["availab", "start", "joining", "immediate"]):
            text = PROFILE_DATA["availability_statement"]
        elif any(k in q for k in ["why", "suitable", "fit", "hire"]):
            text = note
        elif any(k in q for k in ["experience", "skill"]):
            text = PROFILE_DATA["years"] + " years of experience — " + PROFILE_DATA["stack"][:150] + "."
        else:
            text = note
        try:
            page.evaluate(f"""() => {{
              const els = [...document.querySelectorAll('textarea')].filter(e => e.offsetParent !== null);
              const el = els[{i}];
              if (!el) return false;
              const proto = window.HTMLTextAreaElement.prototype;
              Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, {json.dumps(text)});
              el.dispatchEvent(new Event('input', {{bubbles:true}}));
              el.dispatchEvent(new Event('change', {{bubbles:true}}));
              return true;
            }}""")
            n += 1
        except Exception:
            pass
    return n

def upload_resume(page):
    try:
        inp = page.locator("input[type=file]").first
        inp.set_input_files(RESUME, timeout=8000)
        log("resume uploaded")
        return True
    except Exception:
        log("no resume input (profile resume used)")
        return False

def submit(page):
    if dynamic_ui.click(page, "internshala", "submit", timeout_ms=8000):
        log("clicked trusted submit intent")
        return True
    return False

def run_once():
    try:
        runtime = published_runtime("internshala", REQUIRED_FACTS)
        configure_publication(runtime)
        session_snapshot = current_session("internshala")
    except PublicationUnavailable:
        log("published profile/policy not ready — stopping before claim")
        return
    except PortalSessionUnavailable:
        log("session not valid — stopping before claim")
        return
    if not os.path.exists(RESUME):
        log("published resume missing — aborting")
        return
    fails = {}
    profile_dir = tempfile.mkdtemp(
        prefix=f"internshala-{WORKER_ID}-", dir=os.environ.get("TMPDIR")
    )
    atexit.register(shutil.rmtree, profile_dir, ignore_errors=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir, executable_path=CLOAK, headless=True,
            args=["--no-first-run", "--no-default-browser-check",
                  "--disable-blink-features=AutomationControlled", "--window-size=1400,900",
                  "--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={CDP_PORT}"])
        session_revision = inject_current_session(
            ctx, "internshala", expected_revision=session_snapshot.revision
        )
        log(f"session revision {session_revision} pinned")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.add_init_script(STEALTH)
        page.set_default_timeout(30000)

        while True:
            try:
                current = current_session("internshala")
            except PortalSessionUnavailable:
                log("session health changed — stopping before claim")
                break
            if current.revision != session_revision:
                log("session revision changed — restarting before next claim")
                break
            if applied_today() >= DAILY_CAP:
                log(f"daily cap {DAILY_CAP} reached — sleeping 30m")
                time.sleep(1800)
                continue
            try:
                latest_runtime = published_runtime("internshala", REQUIRED_FACTS)
            except PublicationUnavailable:
                log("publication no longer ready — stopping before claim")
                break
            if (latest_runtime.profile_revision != runtime.profile_revision or
                    latest_runtime.preference_set.version != runtime.preference_set.version or
                    latest_runtime.session_revision != runtime.session_revision):
                log("published runtime changed — restarting before next claim")
                break
            job = claim(runtime)
            if not job:
                log("queue empty — sleeping 5m")
                time.sleep(300)
                continue
            log(f"claim: {job['title'][:60]}")
            GUARD = BrowserWatchdog("is_login", max_sec=270, job=(job["id"], job["url"]))
            GUARD.start()
            try:
                page.goto(job["url"], wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(3000)
                cur = page.url
                if "/login" in cur:
                    log("LOGIN WALL — session dead, reverting and stopping")
                    mark(job["id"], "pending")
                    break

                body = page.inner_text("body")[:6000].lower()
                # already applied / closed
                if "already applied" in body or "application submitted" in body:
                    mark(job["id"], "done", "already-applied")
                    log("already applied")
                    audit.record_application("internshala", "", job["title"], job["url"], "already-applied")
                    continue
                if "applications closed" in body or "application deadline" in body and "has passed" in body:
                    mark(job["id"], "skip", "closed")
                    log("skip: closed")
                    continue

                # Intent-first apply navigation; LLM may only choose a low-risk
                # candidate from the sanitized actionable-control inventory.
                clicked = dynamic_ui.hybrid_click(
                    page, "internshala", "apply", CDP_URL,
                    postcondition=lambda: "/student/resume" in page.url or page.locator("form, textarea, input[type=file]").count() > 0,
                )
                if clicked:
                    log("clicked Apply intent")
                if not clicked:
                    mark(job["id"], "skip", "no-apply-button")
                    log("skip: no apply intent")
                    continue

                page.wait_for_timeout(3500)
                # Internshala intermediate: resume check page -> Proceed to application
                if "/student/resume" in page.url:
                    log("resume intermediate page")
                    try:
                        if not dynamic_ui.hybrid_click(
                            page, "internshala", "proceed", CDP_URL,
                            postcondition=lambda: "/student/resume" not in page.url,
                        ):
                            raise RuntimeError("proceed intent failed")
                        log("clicked Proceed to application")
                        page.wait_for_timeout(4000)
                    except Exception as e:
                        log(f"proceed click err: {e}")
                app_body = page.inner_text("body")
                jd_text = app_body[:8000]
                res = jd_match.analyze(jd_text, approved_skills=PROFILE_DATA["stack"], approved_years=PROFILE_DATA["years"], blockers=False)
                if res["decision"] == "skip":
                    mark(job["id"], "skip", res["reason"])
                    log(f"skip: {res['reason']}")
                    continue

                note = PROFILE_DATA["pitch"]
                filled = fill_textareas(page, note)
                log(f"filled {filled} question(s)")
                upload_resume(page)
                audit.snapshot(page, "internshala", job["id"], "before_submit")
                ok = submit(page)
                page.wait_for_timeout(4000)
                after = page.inner_text("body")
                if has_submission_confirmation(after):
                    mark(job["id"], "done", "applied")
                    audit.record_application("internshala", "", job["title"], job["url"], "applied",
                                             answers=note[:300], resume_used=RESUME)
                    log("APPLIED")
                elif ok:
                    fails[job["id"]] = fails.get(job["id"], 0) + 1
                    if fails[job["id"]] >= 2:
                        mark(job["id"], "skip", "submit-unconfirmed")
                        log("skip: submit unconfirmed after retries")
                    else:
                        mark(job["id"], "pending", "submit-unconfirmed")
                        log("submit clicked but confirmation missing; requeued")
                else:
                    fails[job["id"]] = fails.get(job["id"], 0) + 1
                    if fails[job["id"]] >= 2:
                        mark(job["id"], "skip", "apply-flow-fail")
                        log("skip: apply-flow-fail (2 attempts)")
                    else:
                        mark(job["id"], "pending")
                        log("no submit button — reverted to pending")
                    continue
            except Exception as e:
                log(f"error on {job['title'][:40]}: {e}")
                fails[job["id"]] = fails.get(job["id"], 0) + 1
                if fails[job["id"]] >= 2:
                    mark(job["id"], "skip", "error:" + str(e)[:80])
                else:
                    mark(job["id"], "pending")
            finally:
                GUARD.stop()
            if GUARD.fired.is_set():
                mark(job["id"], "pending", "browser-wedge-timeout")
                log("BROWSER WEDGE — job requeued, exiting for systemd restart")
                os._exit(7)
            time.sleep(45 + (int(time.time()) % 45))  # 45-90s human-ish pacing
        try:
            ctx.close()
        finally:
            shutil.rmtree(profile_dir, ignore_errors=True)
            atexit.unregister(shutil.rmtree)

def main():
    while True:
        run_once()
        time.sleep(300)


if __name__ == "__main__":
    main()
