#!/usr/bin/env python3
"""Headless=False: Wellfound Google login + session capture. User logs in interactively."""
from playwright.sync_api import sync_playwright
import json, os
import time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
OUT = "/home/ubuntu/Documents/job_hunt_linkedin/portal_wellfound.json"
STATUS = "/tmp/wf_google_status.json"

def status(**kw):
    json.dump({**kw, "ts": time.strftime("%H:%M:%S")}, open(STATUS, "w"))
    print(json.dumps(kw), flush=True)

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir="/tmp/wf_login_profile", executable_path=CLOAK, headless=False,
        args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
              "--window-size=1500,950"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    status(state="open", msg="LOGIN TO GOOGLE + WELLFOUND IN THE OPEN WINDOW")
    page.goto("https://wellfound.com/login", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    # detect google login completion: wellfound session cookie appears
    for i in range(240):
        time.sleep(10)
        try:
            cookies = ctx.cookies()
            wf = [c for c in cookies if "wellfound.com" in (c.get("domain") or "")]
            goog = [c for c in cookies if "google" in (c.get("domain") or "")]
        except Exception:
            cookies, wf, goog = [], [], []
        if wf and goog:
            json.dump({"cookies": cookies}, open(OUT, "w"))
            status(state="SAVED_GOOGLE", wf=len(wf), google=len(goog), file=OUT)
            time.sleep(3600)
            break
        if i % 6 == 0:
            status(state="waiting", wf=len(wf), google=len(goog))
    ctx.close()
