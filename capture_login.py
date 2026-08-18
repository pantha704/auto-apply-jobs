#!/usr/bin/env python3
"""Login capture: user logs in once, we export storage_state for all workers.
Usage: python3 capture_login.py  (opens headed cloak browser -> LinkedIn -> export on li_at)
"""
from playwright.sync_api import sync_playwright
import json, os
import time,os
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
OUT = "/home/ubuntu/Documents/job_hunt_linkedin/li_state.json"
STATUS = "/tmp/cap_status.json"

def status(**kw):
    json.dump({**kw, "ts": time.strftime("%H:%M:%S")}, open(STATUS, "w"))
    print(json.dumps(kw), flush=True)

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir="/tmp/li_login_capture", executable_path=CLOAK, headless=False,
        args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
              "--window-size=1400,900"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    status(state="open", msg="LOGIN TO LINKEDIN IN THIS WINDOW")
    page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=60000)
    for i in range(180):
        time.sleep(10)
        try:
            has = any(c["name"] == "li_at" for c in ctx.cookies())
        except Exception:
            has = False
        if has:
            state = ctx.storage_state()
            json.dump(state, open(OUT, "w"))
            status(state="SAVED", cookies=len(state.get("cookies", [])), file=OUT)
            time.sleep(3600)
            break
        if i % 6 == 0:
            status(state="waiting", poll=i)
    ctx.close()
