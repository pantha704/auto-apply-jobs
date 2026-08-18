#!/usr/bin/env python3
"""Autonomous Internshala worker — claims internships from the queue, opens the
detail page, applies with an honest cover note + resume upload, audits everything.
Usage: python3 worker_internshala.py <worker_id>
Pacing: DAILY_CAP applications/day (default 40), 45-90s between applies.
"""
import json, os, re, sqlite3, sys, time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright
import audit
import jd_match
from worker_guard import BrowserWatchdog
import profile as ident

HERE = os.path.dirname(os.path.abspath(__file__))
CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
PROFILE = os.path.join(HERE, "profiles", "is_login")
RESUME = os.path.join(HERE, "resume_pratham.pdf")
WORKER_ID = sys.argv[1] if len(sys.argv) > 1 else "is-w1"
DAILY_CAP = int(os.environ.get("IS_DAILY_CAP", "40"))
STEALTH = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

PROFILE_DATA = {
    "phone": ident.PHONE,
    "address": ident.ADDRESS,
    "city": ident.CITY, "state": ident.STATE, "pin": ident.PIN,
    "linkedin": "https://www.linkedin.com/in/pantha704",
    "portfolio": "https://pantha704.github.io",
    "college": ident.COLLEGE,
    "expected": "700000", "current": "480000", "notice": "0",
    "stack": "TypeScript, JavaScript, Python, Rust, Node.js, Next.js, React, Tailwind CSS, PostgreSQL, Prisma, Redis, Docker, Kubernetes, Solana/Anchor, REST APIs, WebSockets",
    "pitch": "Full-stack & Solana engineer in Turbin3 cohort; 4 merged OSS PRs; shipped AI crawler, DeFi credit scoring, RWA platform.",
}

def log(msg):
    print(f"[{WORKER_ID}] [{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def db():
    return sqlite3.connect(os.path.join(HERE, "apply_queue.db"))

def claim():
    c = db()
    row = c.execute("SELECT id, url, title FROM jobs WHERE portal='internshala' AND status='pending' ORDER BY prio DESC, rowid LIMIT 1").fetchone()
    if not row:
        c.close(); return None
    upd = c.execute("UPDATE jobs SET status='claimed', claimed_by=? WHERE id=? AND status='pending'", (WORKER_ID, row[0]))
    c.commit(); c.close()
    if upd.rowcount != 1:
        return claim()
    return {"id": row[0], "url": row[1], "title": row[2]}

def mark(jid, status, result=""):
    c = db()
    c.execute("UPDATE jobs SET status=?, result=? WHERE id=?", (status, result[:200], jid))
    c.commit(); c.close()

def applied_today():
    c = db()
    # Count TODAY's actual applications from the audit table (the source of truth).
    # Bug fixed 2026-08-17: we previously ALSO counted an ALL-TIME branch
    # (`jobs done AND result='applied' AND claimed_by`) and took max() of the two.
    # The all-time count (e.g. 40 from previous days) never reset overnight, so the
    # worker believed it had "hit today's cap 40" forever and slept 30m in a loop
    # while applying ZERO on the current day. Today's date partition is the only
    # correct cap signal.
    n2 = c.execute("SELECT COUNT(*) FROM applications WHERE portal='internshala' AND applied_at LIKE ? AND status='applied'", (time.strftime('%Y-%m-%d') + '%',)).fetchone()[0]
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
            text = "I'm based in Kolkata and fully open to work-from-home/remote arrangements."
        elif any(k in q for k in ["availab", "start", "joining", "immediate"]):
            text = "Available to start immediately."
        elif any(k in q for k in ["why", "suitable", "fit", "hire"]):
            text = note
        elif any(k in q for k in ["experience", "skill"]):
            text = "1 year of full-stack experience — " + PROFILE_DATA["stack"][:150] + "."
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
    for label in ["Submit application", "Submit Application", "Submit", "Apply"]:
        try:
            b = page.locator(f"button:has-text('{label}'), input[type=submit][value*='{label}'], a:has-text('{label}')").first
            if b.is_visible(timeout=2000):
                b.click(timeout=8000)
                log(f"clicked {label}")
                return True
        except Exception:
            continue
    return False

def main():
    if not os.path.exists(RESUME):
        log("RESUME MISSING — aborting")
        sys.exit(1)
    fails = {}
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE, executable_path=CLOAK, headless=True,
            args=["--no-first-run", "--no-default-browser-check",
                  "--disable-blink-features=AutomationControlled", "--window-size=1400,900"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.add_init_script(STEALTH)
        page.set_default_timeout(30000)

        while True:
            if applied_today() >= DAILY_CAP:
                log(f"daily cap {DAILY_CAP} reached — sleeping 30m")
                time.sleep(1800)
                continue
            job = claim()
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

                # find and click Apply Now
                clicked = False
                for sel in ["button:has-text('Apply Now')", "a:has-text('Apply Now')", "button:has-text('Apply now')"]:
                    try:
                        b = page.locator(sel).first
                        if b.is_visible(timeout=2000):
                            b.click(timeout=8000)
                            clicked = True
                            log("clicked Apply Now")
                            break
                    except Exception:
                        continue
                if not clicked:
                    mark(job["id"], "skip", "no-apply-button")
                    log("skip: no apply button")
                    continue

                page.wait_for_timeout(3500)
                # Internshala intermediate: resume check page -> Proceed to application
                if "/student/resume" in page.url:
                    log("resume intermediate page")
                    try:
                        page.click("a:has-text('Proceed to application'), button:has-text('Proceed to application')", timeout=8000)
                        log("clicked Proceed to application")
                        page.wait_for_timeout(4000)
                    except Exception as e:
                        log(f"proceed click err: {e}")
                app_body = page.inner_text("body")
                jd_text = app_body[:8000]
                res = jd_match.analyze(jd_text)
                if res["decision"] == "skip":
                    mark(job["id"], "skip", res["reason"])
                    log(f"skip: {res['reason']}")
                    continue

                note = res["note"]
                filled = fill_textareas(page, note)
                log(f"filled {filled} question(s)")
                upload_resume(page)
                audit.snapshot(page, "internshala", job["id"], "before_submit")
                ok = submit(page)
                page.wait_for_timeout(4000)
                after = page.inner_text("body").lower()
                if "submitted" in after or "application submitted" in after or "success" in after:
                    mark(job["id"], "done", "applied")
                    audit.record_application("internshala", "", job["title"], job["url"], "applied",
                                             answers=note[:300], resume_used=RESUME)
                    log("APPLIED")
                elif ok:
                    mark(job["id"], "done", "submitted-unconfirmed")
                    audit.record_application("internshala", "", job["title"], job["url"], "submitted-unconfirmed",
                                             answers=note[:300], resume_used=RESUME)
                    log("submitted (unconfirmed)")
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

if __name__ == "__main__":
    main()
