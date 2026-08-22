#!/usr/bin/env python3
"""External queue router worker — handles portal='external' jobs from mixed sources.
Claims external jobs and routes them:
  - weworkremotely: tries apply flow, marks dead URLs / login-walls
  - himalayas: Cloudflare-blocked or login-walled → skip with reason
  - yc / naukri: no captured session → needs-login skip
Usage: python3 worker_external.py <worker_id>
"""
import os, re, shutil, sqlite3, sys, tempfile, time, signal
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from worker_guard import BrowserWatchdog
import dynamic_ui
from workflow.worker_telemetry import telemetry_for
from workflow.application_gate import (
    PublicationUnavailable,
    eligible_for_claim,
    pin_claim,
    published_runtime,
)
from workflow.portal_session_runtime import (
    PortalSessionUnavailable,
    current_session,
    inject_current_session,
)
from title_filter import title_rejection_reason

HERE = os.path.dirname(os.path.abspath(__file__))
CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
WORKER_ID = sys.argv[1] if len(sys.argv) > 1 else "ext-w1"
QUEUE_DB = os.getenv("JOBHUNT_QUEUE_DB", os.path.join(HERE, "apply_queue.db"))
STATE_ROOT = os.getenv("JOBHUNT_STATE_ROOT", os.path.join(HERE, "state_queue"))
_worker_match = re.search(r"(\d+)$", WORKER_ID)
CDP_PORT = int(os.environ.get("EXT_CDP_PORT", str(9380 + (int(_worker_match.group(1)) if _worker_match else 1))))
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
PUBLISHED_NOTE = ""
REQUIRED_FACTS = (("cover_note.default", "summary.pitch", "profile.note"),)


def configure_publication(runtime):
    global PUBLISHED_NOTE
    PUBLISHED_NOTE = str(runtime.fact("cover_note.default", "summary.pitch", "profile.note"))

def log(msg):
    print(f"[{WORKER_ID}] [{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def sync_playwright():
    """Load Playwright only when an external browser job actually runs."""
    from playwright.sync_api import sync_playwright as _sync_playwright
    return _sync_playwright()

def db():
    return sqlite3.connect(QUEUE_DB)

def telemetry():
    return telemetry_for(WORKER_ID, "external", QUEUE_DB, STATE_ROOT)

def claim(runtime):
    while True:
        c = db()
        row = c.execute("SELECT id, url, title, source FROM jobs WHERE portal='external' AND status='pending' ORDER BY prio DESC, rowid LIMIT 1").fetchone()
        if not row:
            c.close(); telemetry().idle(); return None
        source = (row[3] or "").lower()
        gate_source = "yc-job" if source == "yc" and "/jobs/" in (row[1] or "") else source
        reason = title_rejection_reason(row[2] or "", gate_source)
        if reason:
            c.execute("UPDATE jobs SET status='skip', result=? WHERE id=? AND status='pending'", (reason, row[0]))
            c.commit(); c.close()
            telemetry().outcome(row[0], "skip", reason)
            continue
        if not eligible_for_claim(runtime, {"title": row[2] or "", "portal": "external", "source": row[3] or ""}):
            c.execute("UPDATE jobs SET status='skip',result='policy-ineligible' WHERE id=? AND status='pending'", (row[0],))
            c.commit(); c.close()
            telemetry().outcome(row[0], "skip", "policy-ineligible")
            continue
        upd_count = pin_claim(c, row[0], WORKER_ID, runtime)
        c.commit(); c.close()
        if upd_count == 1:
            telemetry().claimed(row[0])
            return {"id": row[0], "url": row[1], "title": row[2], "source": row[3]}

def mark(jid, status, result=""):
    c = db()
    c.execute("UPDATE jobs SET status=?, result=? WHERE id=?", (status, result[:200], jid))
    c.commit(); c.close()
    telemetry().outcome(jid, status, result)

def route(job, expected_session_revision=None):
    src = (job.get("source") or "").lower()
    u = job["url"] or ""
    if "weworkremotely.com" in u or src == "weworkremotely":
        return wwr_apply(u)
    if "himalayas.app" in u or src == "himalayas":
        return himalayas_apply(u, expected_session_revision)
    if "workatastartup.com" in u or src == "yc":
        return (False, "needs-login:yc")
    if "naukri.com" in u or src == "naukri":
        return (False, "needs-login:naukri")
    return (False, f"unhandled-source:{src}")

def himalayas_apply(url, expected_session_revision=None):
    """Apply through one isolated context loaded from a pinned canonical revision."""
    if expected_session_revision is None:
        return (False, "himalayas-session-required")
    NOTE = PUBLISHED_NOTE
    SUCCESS_TXT = ("application sent", "has been sent", "application submitted",
                   "we've received your application", "applied successfully")
    profile_dir = tempfile.mkdtemp(
        prefix=f"himalayas-{WORKER_ID}-", dir=os.environ.get("TMPDIR")
    )
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=profile_dir, executable_path=CLOAK, headless=True,
                args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
                      "--window-size=1280,720", "--remote-debugging-address=127.0.0.1",
                      f"--remote-debugging-port={CDP_PORT}"])
            revision = inject_current_session(
                ctx, "himalayas", expected_revision=expected_session_revision
            )
            log(f"himalayas session revision {revision} pinned")
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.set_default_timeout(15000)
            page.set_default_navigation_timeout(30000)
            # success via HTTP: any 2xx POST to an application-ish endpoint
            http_ok = {"hit": False}
            def on_res(r):
                try:
                    if r.request.method == "POST" and r.status in (200, 201, 204) and "himalayas.app" in r.url:
                        if "application" in r.url or "apply" in r.url:
                            http_ok["hit"] = True
                except Exception:
                    pass
            page.on("response", on_res)
            page.goto(url, wait_until="domcontentloaded", timeout=40000)
            # CF challenge handling
            cleared = False
            for i in range(12):
                try:
                    body = page.inner_text("body")[:400].lower()
                except Exception:
                    body = ""
                if "performing security verification" not in body and "verify you are human" not in body:
                    cleared = True
                    break
                try:
                    page.mouse.click(212, 336)
                except Exception:
                    pass
                page.wait_for_timeout(4000)
            if not cleared:
                ctx.close()
                return (False, "cloudflare-wall")
            if "himalayas.app" not in page.url:
                ctx.close()
                return (False, f"redirect:{page.url[:60]}")
            body = page.inner_text("body").lower()
            if "job expired" in body or "no longer accepting" in body or "position has been filled" in body:
                ctx.close()
                return (False, "job-expired")
            # Intent-first entry into the Himalayas application flow.
            if not dynamic_ui.hybrid_click(
                page, "himalayas", "apply", CDP_URL,
                postcondition=lambda: page.locator("form, textarea, input[type=file]").count() > 0 or page.url != url,
            ):
                ctx.close()
                return (False, "no-apply-btn")
            page.wait_for_timeout(7000)
            b2 = page.inner_text("body")
            low2 = b2.lower()
            # location block
            if "location not eligible" in low2:
                ctx.close()
                return (False, "location-block")
            # upsell interstitial
            if "i'm ready to apply" in low2 or "generate cover letter with ai" in low2:
                try:
                    if dynamic_ui.hybrid_click(
                        page, "himalayas", "dismiss_upsell", CDP_URL,
                        postcondition=lambda: "don't show this again" not in page.inner_text("body").lower(),
                    ):
                        page.wait_for_timeout(600)
                    if not dynamic_ui.hybrid_click(
                        page, "himalayas", "ready", CDP_URL,
                        postcondition=lambda: page.locator("textarea, input[type=file]").count() > 0,
                    ):
                        raise RuntimeError("upsell ready intent failed")
                    page.wait_for_timeout(5000)
                    b2 = page.inner_text("body")
                    low2 = b2.lower()
                except Exception as e:
                    log(f"hima upsell err: {str(e)[:60]}")
            # application form?
            ta = page.locator("textarea").first
            if ta.count() > 0 and ta.is_visible():
                try:
                    ta.fill(NOTE)
                except Exception as e:
                    log(f"hima note fill err: {str(e)[:60]}")
                submitted = dynamic_ui.click(page, "himalayas", "submit", timeout_ms=4000)
                if submitted:
                    log("hima submit via trusted intent")
                if not submitted:
                    ctx.close()
                    return (False, "no-send-button")
                page.wait_for_timeout(6000)
                b3 = page.inner_text("body").lower()
                if http_ok["hit"] or any(t in b3 for t in SUCCESS_TXT):
                    ctx.close()
                    return (True, "submitted")
                if "location not eligible" in b3:
                    ctx.close()
                    return (False, "location-block")
                ctx.close()
                return (False, "submit-unconfirmed")
            else:
                ctx.close()
                return (False, "no-apply-form")
    except Exception as e:
        return (False, f"err:{str(e)[:80]}")
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)

def wwr_apply(url):
    """WWR: check job page is alive (not homepage redirect), then attempt apply."""
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=f"/tmp/ext_wwr_{WORKER_ID}", executable_path=CLOAK, headless=True,
                args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
                      "--window-size=1400,900", "--remote-debugging-address=127.0.0.1",
                      f"--remote-debugging-port={CDP_PORT}"])
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.set_default_timeout(15000)
            page.set_default_navigation_timeout(25000)
            page.goto(url, wait_until="domcontentloaded", timeout=40000)
            page.wait_for_timeout(4000)
            if "remote-jobs" not in page.url:
                ctx.close()
                return (False, "dead-url")
            # job page loaded — find apply links
            hrefs = page.evaluate("""() => [...document.querySelectorAll('a')].map(a => [a.innerText.trim().slice(0,40), a.href]).filter(x => /apply|email|send/i.test(x[0]) && x[1]).slice(0,6)""")
            apply_href = None
            for txt, h in hrefs:
                if "apply" in txt.lower() and "login" not in h and "job-seekers" not in h:
                    apply_href = h; break
            if not apply_href:
                for txt, h in hrefs:
                    if "mailto:" in h:
                        apply_href = h; break
            if apply_href:
                if apply_href.startswith("mailto:"):
                    ctx.close()
                    return (False, "email-apply-needs-contact-ingest")
                low_h = apply_href.lower()
                # WWR "Apply" often points at their career-services upsell, not the job.
                if any(x in low_h for x in ("job-copilot", "career-services", "career_services", "/pricing")):
                    ctx.close()
                    return (False, "wwr-upsell-not-apply")
                try:
                    if not dynamic_ui.hybrid_click(
                        page, "weworkremotely", "apply", CDP_URL,
                        postcondition=lambda: page.url != url or len(ctx.pages) > 1,
                    ):
                        ctx.close()
                        return (False, "no-apply-btn")
                    if len(ctx.pages) > 1:
                        page = ctx.pages[-1]
                    page.wait_for_timeout(3000)
                    if "login" in page.url or "sign in" in page.url:
                        ctx.close()
                        return (False, "login-wall")
                    if any(x in page.url.lower() for x in ("job-copilot", "career-services")):
                        ctx.close()
                        return (False, "wwr-upsell-not-apply")
                    ctx.close()
                    return (False, "external-ats-route-required")
                except Exception:
                    ctx.close()
                    return (False, "external-apply-navigation-unknown")
            ctx.close()
            return (False, "no-apply-link")
    except Exception as e:
        return (False, f"err:{str(e)[:80]}")

def next_pending_requires_himalayas_session():
    c = db()
    try:
        row = c.execute(
            "SELECT url,source FROM jobs WHERE portal='external' AND status='pending' "
            "ORDER BY prio DESC,rowid LIMIT 1"
        ).fetchone()
    finally:
        c.close()
    if not row:
        return False
    return "himalayas.app" in (row[0] or "") or (row[1] or "").lower() == "himalayas"


def main():
    def _alarm(signum, frame):
        raise TimeoutError("job hard timeout")
    signal.signal(signal.SIGALRM, _alarm)
    while True:
        requires_himalayas = next_pending_requires_himalayas_session()
        try:
            runtime = published_runtime(
                "himalayas" if requires_himalayas else None,
                REQUIRED_FACTS,
                require_session=requires_himalayas,
            )
            configure_publication(runtime)
        except PublicationUnavailable:
            log("published profile/policy/session not ready — waiting before claim")
            time.sleep(300)
            continue
        job = claim(runtime)
        if not job:
            log("queue empty, sleep")
            time.sleep(60); continue
        log(f"claim: {job['title'][:50]} [{job['source']}]")
        signal.alarm(150)
        GUARD = BrowserWatchdog([f"ext_hima_{WORKER_ID}", f"ext_wwr_{WORKER_ID}"], max_sec=210,
                                job=(job["id"], job["url"]))
        GUARD.start()
        try:
            ok, reason = route(
                job,
                runtime.session_revision if runtime.session_revision > 0 else None,
            )
        except Exception as e:
            ok, reason = False, f"hard-timeout|{str(e)[:60]}"
        finally:
            signal.alarm(0)
            GUARD.stop()
        if GUARD.fired.is_set():
            mark(job["id"], "pending", "browser-wedge-timeout")
            log("BROWSER WEDGE — job requeued, exiting for systemd restart")
            os._exit(7)
        mark(job["id"], "done" if ok else "skip", reason)
        log(f"{'DONE' if ok else 'SKIP'}: {reason[:90]}")
        time.sleep(3)

if __name__ == "__main__":
    main()
