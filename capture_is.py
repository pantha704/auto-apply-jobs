#!/usr/bin/env python3
"""Capture Internshala login via saved Google session (no OTP needed).
Copies li_login_profile_fresh -> profiles/is_login, clicks Login with Google,
waits for session, saves cookies to portal_internshala.json."""
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

def main():
    if os.path.exists(DST):
        shutil.rmtree(DST)
    shutil.copytree(SRC, DST)
    log("profile copied")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=DST, executable_path=CLOAK, headless=True,
            args=["--no-first-run", "--no-default-browser-check",
                  "--disable-blink-features=AutomationControlled", "--window-size=1400,900"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.add_init_script(STEALTH)

        log("goto internshala login")
        page.goto("https://internshala.com/login/user", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

        # click Login with Google (may open popup)
        popup = None
        def on_popup(pg):
            nonlocal popup
            popup = pg
            log("popup opened: " + pg.url[:80])
        page.on("popup", on_popup)

        try:
            with page.expect_navigation(wait_until="domcontentloaded", timeout=15000):
                page.click("a:has-text('Login with Google')", timeout=10000)
            log("navigated same-tab")
        except Exception:
            log("no same-tab nav (popup path)")

        active = popup or page
        # ride the Google OAuth flow: click account row / Continue if shown
        for i in range(20):
            cur = active.url
            log(f"[{i}] url: {cur[:90]}")
            if "internshala.com" in cur and ("dashboard" in cur or "applications" in cur or "login" not in cur.split("/")[-1]):
                break
            try:
                if "accounts.google.com" in cur:
                    clicked = False
                    for sel in ["div[role=link]:has-text('@')", "button:has-text('Continue')", "span:has-text('Continue')", "button:has-text('Next')"]:
                        try:
                            active.click(sel, timeout=4000)
                            log("clicked " + sel)
                            clicked = True
                            break
                        except Exception:
                            continue
                    if not clicked:
                        log("google page, nothing to click")
                elif "internshala.com/login" in cur:
                    # still on login — try google link again in active page
                    try:
                        active.click("a:has-text('Login with Google')", timeout=4000)
                        log("re-clicked google link")
                    except Exception:
                        pass
            except Exception as e:
                log(f"step err: {e}")
            time.sleep(5)

        time.sleep(4)
        cookies = ctx.cookies()
        is_cookies = [c for c in cookies if "internshala.com" in (c.get("domain") or "")]
        log(f"total cookies: {len(cookies)}, internshala: {len(is_cookies)}")
        json.dump({"cookies": cookies, "url": active.url,
                   "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                  open(OUT, "w"), indent=1)
        log("saved -> " + OUT)
        log("final url: " + active.url)

if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
