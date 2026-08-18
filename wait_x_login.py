#!/usr/bin/env python3
"""Open X search in CloakBrowser, poll until user logs in, then keep session alive."""
from playwright.sync_api import sync_playwright
import json, os
import time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
STATUS = "/tmp/x_login_status.json"
SEARCH = "https://x.com/search?q=" + "%22we%27re+hiring%22+OR+%22hiring%22+(solana+OR+web3+OR+rust+OR+%22full+stack%22+OR+typescript)+lang%3Aen&f=live"

def status(**kw):
    kw["ts"] = time.strftime("%H:%M:%S")
    json.dump(kw, open(STATUS, "w"))
    print(json.dumps(kw), flush=True)

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir="/tmp/cloak_profile", executable_path=CLOAK, headless=False,
        args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
              "--window-size=1400,900"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    status(state="opening")
    page.goto(SEARCH, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4000)
    status(state="waiting_for_login", url=page.url[:100])
    for i in range(150):
        time.sleep(10)
        try:
            url = page.url
            logged = "x.com/home" in url or "x.com/search" in url
        except Exception:
            logged, url = False, "?"
        if logged and i > 3:
            status(state="ON_X", url=url)
            time.sleep(3600)
            break
        if i % 6 == 0:
            status(state="waiting_for_login", url=url, poll=i)
    ctx.close()
