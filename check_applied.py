#!/usr/bin/env python3
"""One-off: read LO's Wellfound Applied page with stored session cookies and count applications."""
import json, os
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright

HERE = "/home/ubuntu/job_hunt_linkedin"
CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
STATE = os.path.join(HERE, "portal_wellfound.json")

state = json.load(open(STATE))
cookies = state.get("cookies", [])
print("cookies loaded:", len(cookies))
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir="/tmp/cloak_profile_check", executable_path=CLOAK, headless=True,
        args=["--no-first-run", "--disable-blink-features=AutomationControlled", "--window-size=1400,900"])
    ctx.add_cookies(cookies)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://wellfound.com/applied", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(9000)
    try:
        body = page.inner_text("body")[:2500]
    except Exception as e:
        body = f"ERR {e}"
    counts = {}
    for sel in ["a[href*='/jobs/']", "[data-testid*=application]", "[data-testid*=Applied]", "div[class*=ApplicationCard]"]:
        try:
            counts[sel] = page.locator(sel).count()
        except Exception:
            counts[sel] = -1
    print("URL:", page.url)
    print("SELECTOR COUNTS:", counts)
    print("BODY:", body[:1500].replace("\n", " | "))
    page.screenshot(path="/tmp/applied_page.png")
    ctx.close()
