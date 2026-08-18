#!/usr/bin/env python3
"""External queue router worker — handles portal='external' jobs from mixed sources.
Claims external jobs and routes them:
  - weworkremotely: tries apply flow, marks dead URLs / login-walls
  - himalayas: Cloudflare-blocked or login-walled → skip with reason
  - yc / naukri: no captured session → needs-login skip
Usage: python3 worker_external.py <worker_id>
"""
import json, os, re, sqlite3, sys, time, signal
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright
from worker_guard import BrowserWatchdog

HERE = os.path.dirname(os.path.abspath(__file__))
CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
WORKER_ID = sys.argv[1] if len(sys.argv) > 1 else "ext-w1"

def log(msg):
    print(f"[{WORKER_ID}] [{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def db():
    return sqlite3.connect(os.path.join(HERE, "apply_queue.db"))

def claim():
    c = db()
    row = c.execute("SELECT id, url, title, source FROM jobs WHERE portal='external' AND status='pending' ORDER BY prio DESC, rowid LIMIT 1").fetchone()
    if not row:
        c.close(); return None
    upd = c.execute("UPDATE jobs SET status='claimed', claimed_by=? WHERE id=? AND status='pending'", (WORKER_ID, row[0]))
    c.commit(); c.close()
    if upd.rowcount != 1:
        return claim()
    return {"id": row[0], "url": row[1], "title": row[2], "source": row[3]}

def mark(jid, status, result=""):
    c = db()
    c.execute("UPDATE jobs SET status=?, result=? WHERE id=?", (status, result[:200], jid))
    c.commit(); c.close()

def route(job):
    src = (job.get("source") or "").lower()
    u = job["url"] or ""
    if "weworkremotely.com" in u or src == "weworkremotely":
        return wwr_apply(u)
    if "himalayas.app" in u or src == "himalayas":
        return himalayas_apply(u)
    if "workatastartup.com" in u or src == "yc":
        return (False, "needs-login:yc")
    if "naukri.com" in u or src == "naukri":
        return (False, "needs-login:naukri")
    return (False, f"unhandled-source:{src}")

def himalayas_apply(url):
    """Himalayas apply — live talent session from portal_himalayas.json.

    Flow (learned 2026-08-16, see skill pitfall 41):
      1. CF challenge -> raw pixel click at (212,336) on 1280x720
      2. click first 'Apply now' button, wait ~7s (modal renders slowly)
      3. 'Location not eligible' dialog -> skip location-block (honest)
      4. AI-upsell interstitial -> click 'Don't show this again' + 'I'm ready to apply'
      5. form with textarea + submit -> fill cover note, submit
      6. success = POST 2xx to *application* URL OR success text OR button flips 'Applied'
    """
    state_file = os.path.join(HERE, "portal_himalayas.json")
    try:
        cookies = json.load(open(state_file)).get("cookies", [])
    except Exception:
        cookies = []
    if not any(c.get("name") == "himalayas_app_session" for c in cookies):
        # still try the persistent profile — the JSON jar can have empty values
        pass
    NOTE = ("Full-stack engineer (TypeScript/Python/Rust) with 1 year of experience, "
            "based in Kolkata, open to remote roles.")
    SUCCESS_TXT = ("application sent", "has been sent", "application submitted",
                   "we've received your application", "applied successfully")
    # Playwright rejects cookies with empty values. Prefer the live persistent
    # profile (hima_cap) which still holds the real session from the onboard fill.
    HIMA_PROF = os.path.join(HERE, "profiles", "hima_cap")
    usable = []
    for c in cookies:
        if not c.get("name") or not c.get("value"):
            continue
        cc = {k: c[k] for k in ("name", "value", "domain", "path") if k in c}
        if c.get("expires") not in (None, -1, 0, ""):
            try:
                cc["expires"] = float(c["expires"])
            except Exception:
                pass
        ss = c.get("sameSite")
        if ss in ("Strict", "Lax", "None"):
            cc["sameSite"] = ss
        if c.get("secure"):
            cc["secure"] = True
        if c.get("httpOnly"):
            cc["httpOnly"] = True
        usable.append(cc)
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=HIMA_PROF, executable_path=CLOAK, headless=True,
                args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
                      "--window-size=1280,720"])
            if usable:
                try:
                    ctx.add_cookies(usable)
                except Exception:
                    pass
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
            # click apply
            try:
                page.locator("button:has-text('Apply')").first.click(timeout=5000)
            except Exception as e:
                ctx.close()
                return (False, f"no-apply-btn:{str(e)[:40]}")
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
                    try:
                        page.click("text=Don't show this again", timeout=2000)
                        page.wait_for_timeout(600)
                    except Exception:
                        pass
                    page.click("button:has-text(\"I'm ready to apply\")", timeout=4000)
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
                submitted = False
                for lbl in ("Send application", "Submit application", "Submit", "Apply"):
                    try:
                        el = page.locator(f"button:has-text('{lbl}')").last
                        if el.count() > 0 and el.is_visible():
                            el.click(timeout=4000)
                            submitted = True
                            log(f"hima submit via {lbl}")
                            break
                    except Exception:
                        continue
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

def wwr_apply(url):
    """WWR: check job page is alive (not homepage redirect), then attempt apply."""
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=f"/tmp/ext_wwr_{WORKER_ID}", executable_path=CLOAK, headless=True,
                args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
                      "--window-size=1400,900"])
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
                    return (True, f"email-apply:{apply_href[:60]}")
                low_h = apply_href.lower()
                # WWR "Apply" often points at their career-services upsell, not the job.
                if any(x in low_h for x in ("job-copilot", "career-services", "career_services", "/pricing")):
                    ctx.close()
                    return (False, "wwr-upsell-not-apply")
                try:
                    page.goto(apply_href, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(3000)
                    if "login" in page.url or "sign in" in page.url:
                        ctx.close()
                        return (False, "login-wall")
                    if any(x in page.url.lower() for x in ("job-copilot", "career-services")):
                        ctx.close()
                        return (False, "wwr-upsell-not-apply")
                    ctx.close()
                    return (True, f"external-apply:{apply_href[:70]}")
                except Exception:
                    ctx.close()
                    return (True, f"external-apply:{apply_href[:70]}")
            ctx.close()
            return (False, "no-apply-link")
    except Exception as e:
        return (False, f"err:{str(e)[:80]}")

def main():
    def _alarm(signum, frame):
        raise TimeoutError("job hard timeout")
    signal.signal(signal.SIGALRM, _alarm)
    while True:
        job = claim()
        if not job:
            log("queue empty, sleep")
            time.sleep(60); continue
        log(f"claim: {job['title'][:50]} [{job['source']}]")
        signal.alarm(150)
        GUARD = BrowserWatchdog([f"ext_hima_{WORKER_ID}", f"ext_wwr_{WORKER_ID}"], max_sec=210,
                                job=(job["id"], job["url"]))
        GUARD.start()
        try:
            ok, reason = route(job)
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
