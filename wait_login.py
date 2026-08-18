#!/usr/bin/env python3
"""Stealth Chrome launch — spoof automation signals, keep LinkedIn session."""
from playwright.sync_api import sync_playwright
import json, os
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome", time, os

PROFILE = "/home/ubuntu/.config/google-chrome/Profile 4"
STATUS = "/tmp/li_login_status.json"
LINKEDIN = "https://www.linkedin.com/feed/"

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || {runtime: {}};
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
const origQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (p) =>
  p.name === 'notifications'
    ? Promise.resolve({state: Notification.permission})
    : origQuery(p);
"""

def status(**kw):
    kw["ts"] = time.strftime("%H:%M:%S")
    json.dump(kw, open(STATUS, "w"))
    print(json.dumps(kw), flush=True)

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=PROFILE, executable_path=CLOAK, headless=False,
        args=[
            "--no-first-run", "--no-default-browser-check", "--disable-sync",
            "--disable-features=Translate,AutomationControlled",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars", "--window-size=1400,900",
            "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        ],
    )
    ctx.add_init_script(STEALTH_JS)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    status(state="opening")
    page.goto(LINKEDIN, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    status(state="waiting_for_login", url=page.url)
    for i in range(150):
        time.sleep(10)
        try:
            has = any(c["name"] == "li_at" for c in ctx.cookies())
            url = page.url
        except Exception:
            has, url = False, "?"
        if has:
            status(state="LOGGED_IN", url=url)
            page.screenshot(path="/tmp/li_loggedin.png")
            time.sleep(3600)
            break
        if i % 6 == 0:
            status(state="waiting_for_login", url=url, poll=i)
    ctx.close()
