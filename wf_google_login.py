import os
#!/usr/bin/env python3
"""Automated Google login for Wellfound: fill email/password, capture Google+WF state."""
from playwright.sync_api import sync_playwright
import json, os
import time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
OUT = "/home/ubuntu/Documents/job_hunt_linkedin/portal_wellfound.json"
STATUS = "/tmp/wf_auto_status.json"
EMAIL = os.environ.get("JOBHUNT_EMAIL", "")
PASSWORD = os.environ.get("GOOGLE_PASSWORD", "")
def status(**kw):
    json.dump({**kw, "ts": time.strftime("%H:%M:%S")}, open(STATUS, "w"))
    print(json.dumps(kw), flush=True)

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir="/tmp/wf_auto_login", executable_path=CLOAK, headless=False,
        args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
              "--window-size=1500,950"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    status(state="start")
    page.goto("https://wellfound.com/login", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4000)
    # click "Continue with Google" / "Log in with Google"
    clicked = False
    for sel in ["button:has-text('Continue with Google')", "button:has-text('Log in with Google')",
                "a:has-text('Continue with Google')", "button:has-text('Google')"]:
        try:
            page.click(sel, timeout=4000)
            clicked = True
            break
        except Exception:
            continue
    status(state="google_clicked" if clicked else "no-google-btn", url=page.url[:80])
    page.wait_for_timeout(5000)
    status(state="after", url=page.url[:100])
    # Google accounts flow
    for step in range(12):
        url = page.url
        if "accounts.google.com" in url:
            body = page.inner_text("body")[:300]
            # email field
            try:
                if "Enter your email" in body or page.locator("input[type=email]").count():
                    page.fill("input[type=email]", EMAIL)
                    page.wait_for_timeout(800)
                    try: page.click("button:has-text('Next')", timeout=3000)
                    except Exception: page.keyboard.press("Enter")
                    page.wait_for_timeout(3000)
                    status(state="email_filled", step=step)
            except Exception:
                pass
            try:
                body = page.inner_text("body")[:300]
                if "Enter your password" in body or page.locator("input[type=password]").count():
                    page.fill("input[type=password]", PASSWORD)
                    page.wait_for_timeout(800)
                    try: page.click("button:has-text('Next')", timeout=3000)
                    except Exception: page.keyboard.press("Enter")
                    page.wait_for_timeout(4000)
                    status(state="password_filled", step=step)
            except Exception:
                pass
            # 2FA / verification detection
            body = page.inner_text("body")
            if any(k in body.lower() for k in ["verify", "two-step", "2-step", "authenticator", "code", "get a verification"]):
                status(state="2FA_NEEDED", url=url[:90])
                time.sleep(3600)
                break
        elif "wellfound" in url and "login" not in url:
            break
        page.wait_for_timeout(2500)
    # capture
    time.sleep(3000)
    cookies = ctx.cookies()
    wf = [c for c in cookies if "wellfound.com" in (c.get("domain") or "")]
    goog = [c for c in cookies if "google" in (c.get("domain") or "")]
    json.dump({"cookies": cookies}, open(OUT, "w"))
    status(state="SAVED", wf=len(wf), google=len(goog), url=page.url[:80])
    time.sleep(3600)
    ctx.close()
