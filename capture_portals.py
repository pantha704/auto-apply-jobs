#!/usr/bin/env python3
"""Capture portal logins (Wellfound/YC/Naukri/Internshala) into per-site state files."""
from playwright.sync_api import sync_playwright
import json, os
import time,os
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
OUT = "/home/ubuntu/Documents/job_hunt_linkedin/"
STATUS = "/tmp/portal_status.json"
PORTALS = [
    ("wellfound", "https://wellfound.com/login"),
    ("yc", "https://www.workatastartup.com/users/sign_in"),
    ("naukri", "https://www.naukri.com/nlogin/login"),
    ("internshala", "https://internshala.com/register"),
]
AUTH_HINTS = {
    "wellfound": "wellfound.com",
    "yc": "workatastartup.com",
    "naukri": "naukri.com",
    "internshala": "internshala.com",
}

def status(**kw):
    json.dump({**kw, "ts": time.strftime("%H:%M:%S")}, open(STATUS, "w"))
    print(json.dumps(kw), flush=True)

captured = {}
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir="/tmp/cloak_profile", executable_path=CLOAK, headless=False,
        args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
              "--window-size=1500,950"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    status(state="open", msg="LOGIN TO THE PORTALS IN THE OPEN WINDOWS")
    for name, url in PORTALS:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2500)
        except Exception:
            pass
    # poll: detect login per portal by checking cookies
    for i in range(180):
        time.sleep(12)
        try:
            cookies = ctx.cookies()
        except Exception:
            cookies = []
        for name in [p[0] for p in PORTALS]:
            if name in captured:
                continue
            dom = AUTH_HINTS[name]
            has = any(dom in (c.get("domain") or "") for c in cookies)
            # require more than tracking: session-ish cookies
            ses = [c for c in cookies if dom in (c.get("domain") or "") and c.get("name") in ("_wellfound_session", "session", "_wapp_session", "user_session", "auth", "token", "_nauki_session", "Naukri_Profile_id", "nk_g_session", "session_id", "_is_session") or "session" in (c.get("name") or "").lower()]
            if has and ses:
                captured[name] = True
                json.dump({"cookies": cookies}, open(OUT + f"portal_{name}.json", "w"))
                status(portal=name, cookies=len(cookies))
        if len(captured) == len(PORTALS):
            status(state="ALL_CAPTURED")
            time.sleep(3600)
            break
        if i % 5 == 0:
            status(state="waiting", captured=list(captured.keys()))
    ctx.close()
