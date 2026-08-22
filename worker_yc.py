#!/usr/bin/env python3
"""Work at a Startup (YC) apply worker.

Claims portal='yc' jobs. Company pages (/companies/<slug>) get expanded into
their /jobs/<id> listings (inserted, deduped). Job pages go through the apply
flow: click Apply -> cover-note modal -> Send, with honest triage:
  - jd_match blockers (citizens-only, clearance, stack-mismatch) -> skip
  - title filter (non-tech) -> skip
  - already-applied detection -> skip
Audits every attempt via audit.record_application.

Usage: python3 worker_yc.py <worker_id>
"""
import json, os, re, sqlite3, sys, time, signal
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from worker_guard import BrowserWatchdog
from playwright.sync_api import sync_playwright
import audit
import dynamic_ui
import jd_match
from job_identity import stable_job_id
from submission_signals import has_submission_confirmation
from title_filter import title_rejection_reason

HERE = os.path.dirname(os.path.abspath(__file__))
CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
PROFILE = os.path.join(HERE, "profiles", "yc_cap")
STATE = os.path.join(HERE, "portal_yc.json")
RESUME = "/home/ubuntu/Documents/Pratham_Jaiswal_Updated_Resume.pdf"
WORKER_ID = sys.argv[1] if len(sys.argv) > 1 else "yc-w1"
_worker_match = re.search(r"(\d+)$", WORKER_ID)
CDP_PORT = int(os.environ.get("YC_CDP_PORT", str(9360 + (int(_worker_match.group(1)) if _worker_match else 1))))
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
STEALTH = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

RUNNING = True
def _h(signum, frame):
    global RUNNING
    RUNNING = False
signal.signal(signal.SIGTERM, _h)
signal.signal(signal.SIGINT, _h)

def log(msg):
    print(f"[{WORKER_ID}] [{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def db():
    return sqlite3.connect(os.path.join(HERE, "apply_queue.db"))

def claim():
    c = db()
    row = c.execute("SELECT id, url, title FROM jobs WHERE portal='yc' AND status='pending' ORDER BY prio DESC, rowid LIMIT 1").fetchone()
    if not row:
        c.close()
        return None
    upd = c.execute("UPDATE jobs SET status='claimed', claimed_by=? WHERE id=? AND status='pending'", (WORKER_ID, row[0]))
    c.commit()
    c.close()
    if upd.rowcount != 1:
        return claim()
    return {"id": row[0], "url": row[1], "title": row[2]}

def mark(jid, status, result=""):
    c = db()
    c.execute("UPDATE jobs SET status=?, result=? WHERE id=?", (status, result[:250], jid))
    c.commit()
    c.close()

def expand_company(page, job):
    """Company page -> enqueue its /jobs/ links, mark company done."""
    links = page.evaluate("""() => [...document.querySelectorAll('a[href*="/jobs/"]')].map(a => a.href).filter(h => /\\/jobs\\/\\d+/.test(h))""")
    links = list(dict.fromkeys(links))
    if not links:
        mark(job["id"], "skip", "no-jobs-on-company-page")
        return
    c = db()
    added = 0
    for l in links:
        jid = stable_job_id("yc", l)
        ttl = re.sub(r"^.*?/jobs/", "", l)
        cur = c.execute("SELECT id FROM jobs WHERE id=? OR url=?", (jid, l)).fetchone()
        if cur:
            continue
        c.execute("INSERT OR IGNORE INTO jobs (id, portal, url, title, source, status, prio, fetched_at) VALUES (?,?,?,?,?, 'pending', 5, ?)",
                  (jid, "yc", l, ttl, "yc", time.strftime("%Y-%m-%dT%H:%M:%SZ")))
        added += 1
    c.commit()
    c.close()
    mark(job["id"], "done", f"expanded:{added}")

def apply_job(page, job):
    """Job page -> Apply modal -> Send. Returns (status, result, note)."""
    page.wait_for_timeout(2500)
    body = ""
    try:
        body = page.inner_text("body")
    except Exception:
        pass

    # already applied?
    if re.search(r"applied\b|already applied", body, re.I) and "a:has-text('Apply')" not in str(page.query_selector("a:has-text('Apply')")):
        return ("skip", "already-applied", "")

    # JD match triage (blockers: citizens-only, clearance, stack-mismatch)
    res = jd_match.analyze(body[:6000])
    if res["decision"] == "skip":
        return ("skip", res["reason"], "")

    # title filter (in case slug-derived title was misleading)
    h1 = ""
    try:
        h1 = page.inner_text("h1")[:120]
    except Exception:
        pass
    title_reason = title_rejection_reason(h1, "yc-job")
    if title_reason:
        return ("skip", title_reason, "")

    # Intent-first apply navigation; no model fallback is allowed to send.
    if not dynamic_ui.hybrid_click(
        page, "yc", "apply", CDP_URL,
        postcondition=lambda: page.locator("textarea, [role=dialog]").count() > 0,
    ):
        return ("skip", "no-apply-button", "")

    # wait for the message modal
    ta = None
    for _ in range(10):
        try:
            ta = page.query_selector("textarea")
            if ta and ta.is_visible():
                break
        except Exception:
            pass
        page.wait_for_timeout(1000)
    if not ta:
        return ("skip", "no-apply-modal", "")

    note = res["note"] or "Hi! I'm Pratham Jaiswal, a full-stack developer building with TypeScript, React, Node.js and Python. I'd love to contribute to your team."
    try:
        ta.click()
        ta.fill(note)
        page.wait_for_timeout(800)
    except Exception as e:
        return ("skip", f"fill-err:{str(e)[:60]}", "")

    # Capture application-scoped mutation responses before Send.
    submit_http = []
    try:
        def _on_resp(r):
            try:
                if r.request.method in ("POST", "PUT") and re.search(r"application|apply|send|candidate", r.url):
                    submit_http.append(r.status)
            except Exception:
                pass
        page.on("response", _on_resp)
    except Exception:
        pass
    # Send
    if not dynamic_ui.click(page, "yc", "send", timeout_ms=5000):
        return ("skip", "no-send-button", "")

    page.wait_for_timeout(4000)
    try:
        after = page.inner_text("body")[:3000]
        if has_submission_confirmation(after, submit_http):
            return ("submitted", "sent", note)
    except Exception:
        pass
    return ("skip", "send-unconfirmed", "")

def apply_url(url):
    """Standalone apply attempt for one YC URL (used by the reviewer worker).
    Returns (status, result, note) — never touches the queue."""
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=PROFILE, executable_path=CLOAK, headless=True,
                args=["--no-first-run", "--no-default-browser-check",
                      "--disable-blink-features=AutomationControlled", "--window-size=1400,900",
                      "--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={CDP_PORT}"])
            if os.path.exists(STATE):
                try:
                    ctx.add_cookies(json.load(open(STATE)).get("cookies", []))
                except Exception:
                    pass
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.add_init_script(STEALTH)
            page.set_default_timeout(20000)
            page.set_default_navigation_timeout(40000)
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            if re.search(r"/companies/[^/]+$", url) and not re.search(r"/jobs/\d+", url):
                ctx.close()
                return ("skip", "company-page", "")
            res = apply_job(page, {"url": url})
            ctx.close()
            return res
    except Exception as e:
        return ("skip", f"error|{str(e)[:100]}", "")


def handle(job):
    url = job["url"] or ""
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=PROFILE, executable_path=CLOAK, headless=True,
                args=["--no-first-run", "--no-default-browser-check",
                      "--disable-blink-features=AutomationControlled", "--window-size=1400,900",
                      "--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={CDP_PORT}"])
            # fallback: inject banked session cookies too
            if os.path.exists(STATE):
                try:
                    ctx.add_cookies(json.load(open(STATE)).get("cookies", []))
                except Exception:
                    pass
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.add_init_script(STEALTH)
            page.set_default_timeout(20000)
            page.set_default_navigation_timeout(40000)
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)

            if re.search(r"/companies/[^/]+$", url) and not re.search(r"/jobs/\d+", url):
                expand_company(page, job)
            else:
                status, result, note = apply_job(page, job)
                if status == "submitted":
                    comp = ""
                    m = re.search(r"at\s+([A-Za-z0-9&.\- ]+?)(?:\(W\d+\))?\s*$", (page.inner_text("h1") or "")[:150])
                    if m:
                        comp = m.group(1).strip()
                    audit.record_application("yc", comp or job["title"], job["title"], url,
                                             "submitted", answers={"note": note[:400]}, resume_used=RESUME,
                                             note=result)
                    mark(job["id"], "done", "submitted")
                else:
                    mark(job["id"], "skip", result)
            ctx.close()
    except Exception as e:
        mark(job["id"], "pending", "")  # requeue on infra errors
        log(f"err on {url}: {str(e)[:120]}")
        time.sleep(3)

def main():
    log(f"started (profile={PROFILE})")
    idle = 0
    while RUNNING:
        job = claim()
        if not job:
            idle += 1
            # WAIT instead of exiting: an empty queue must NOT crash-loop the
            # worker (NRestarts was 780 — systemd revived it every 4s, burned
            # CPU + restart counters, silently produced nothing). Sleep so a
            # later refill/injection is picked up by THIS process.
            if RUNNING:
                time.sleep(25)
            continue
        idle = 0
        log(f"claim: {job['title'][:70]} | {job['url'][:80]}")
        GUARD = BrowserWatchdog("yc_cap", max_sec=240, job=(job["id"], job["url"]))
        GUARD.start()
        try:
            handle(job)
        finally:
            GUARD.stop()
        if GUARD.fired.is_set():
            mark(job["id"], "pending", "browser-wedge-timeout")
            log("BROWSER WEDGE — job requeued, exiting for systemd restart")
            os._exit(7)
        time.sleep(6 + (idle * 0))  # gentle pacing
    log("stopped")

if __name__ == "__main__":
    main()
