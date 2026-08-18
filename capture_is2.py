#!/usr/bin/env python3
"""Diagnose + capture Google->Internshala OAuth. Calm, screenshot at each stage."""
import json, os, shutil, sys, time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
HERE = "/home/ubuntu/job_hunt_linkedin"
SRC = "/home/ubuntu/tmp_chrome/li_login_profile_fresh"
DST = os.path.join(HERE, "profiles", "is_login")
OUT = os.path.join(HERE, "portal_internshala.json")
STEALTH = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def shot(page, name):
    try:
        page.screenshot(path=f"/tmp/is_{name}.png")
        log(f"shot -> /tmp/is_{name}.png")
    except Exception as e:
        log(f"shot err {e}")

def main():
    if os.path.exists(DST):
        shutil.rmtree(DST)
    shutil.copytree(SRC, DST)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=DST, executable_path=CLOAK, headless=True,
            args=["--no-first-run", "--no-default-browser-check",
                  "--disable-blink-features=AutomationControlled", "--window-size=1400,900"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.add_init_script(STEALTH)

        # 1. check google session state
        page.goto("https://accounts.google.com/", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(4000)
        shot(page, "1_google_state")
        try:
            who = page.evaluate("""() => {
              const el = document.querySelector('a[aria-label*="Google Account"], a[href*="accounts.google.com/SignOutOptions"], .gb_b a[aria-label], img[alt*="account"]');
              return document.title + ' | ' + (el ? (el.getAttribute('aria-label')||el.textContent||'') : 'no-account-el');
            }""")
            log("google state: " + str(who)[:120])
        except Exception as e:
            log(f"google probe err: {e}")

        # 2. internshala login -> google
        page.goto("https://internshala.com/login/user", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)
        shot(page, "2_is_login")
        try:
            with page.expect_navigation(wait_until="domcontentloaded", timeout=15000):
                page.click("a:has-text('Login with Google')", timeout=10000)
        except Exception:
            log("click via popup or slow nav")

        # 3. chooser: dump account rows, click the right one once, wait
        for stage in range(6):
            cur = page.url
            log(f"[{stage}] {cur[:80]}")
            if "internshala.com" in cur and "/login" not in cur:
                log("LANDED ON INTERNSHALA")
                break
            if "accountchooser" in cur or "accounts.google.com" in cur:
                shot(page, f"3_chooser_{stage}")
                rows = page.evaluate("""() => {
                  return [...document.querySelectorAll('div[role=link], a[role=link], li')]
                    .map(e => (e.getAttribute('aria-label')||e.innerText||'').trim())
                    .filter(t => t.includes('@') && t.length < 120);
                }""")
                log("rows: " + json.dumps(rows[:5]))
                if rows:
                    try:
                        page.click(f"div[role=link]:has-text('{rows[0].split('@')[0][:12]}')", timeout=8000)
                        log("clicked account row once")
                        page.wait_for_timeout(15000)
                        shot(page, f"4_after_click_{stage}")
                        cur = page.url
                        log(f"after click: {cur[:90]}")
                        if "consent" in cur or "internshala" in cur:
                            if "consent" in cur:
                                shot(page, "5_consent")
                                for sel in ["button:has-text('Continue')", "button:has-text('Allow')", "button[type=submit]"]:
                                    try:
                                        page.click(sel, timeout=6000)
                                        log(f"consent clicked: {sel}")
                                        page.wait_for_timeout(15000)
                                        break
                                    except Exception:
                                        continue
                            page.wait_for_timeout(8000)
                            cur = page.url
                            if "internshala.com" in cur and "/login" not in cur:
                                log("LOGGED IN")
                                break
                    except Exception as e:
                        log(f"row click err: {e}")
            time.sleep(6)

        time.sleep(4)
        cookies = ctx.cookies()
        is_c = [c for c in cookies if "internshala.com" in (c.get("domain") or "")]
        log(f"cookies: total {len(cookies)}, internshala {len(is_c)}")
        json.dump({"cookies": cookies, "url": page.url,
                   "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                  open(OUT, "w"), indent=1)
        log("final: " + page.url[:100])
        shot(page, "6_final")

if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
