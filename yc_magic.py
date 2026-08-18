#!/usr/bin/env python3
"""Trigger YC email magic link for os.environ.get("JOBHUNT_EMAIL","")."""
import os, time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
HERE = "/home/ubuntu/job_hunt_linkedin"
DST = os.path.join(HERE, "profiles", "yc_cap")
STEALTH = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
EMAIL = os.environ.get("JOBHUNT_EMAIL", "")

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=DST, executable_path=CLOAK, headless=True,
        args=["--no-first-run", "--no-default-browser-check",
              "--disable-blink-features=AutomationControlled", "--window-size=1400,900"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.add_init_script(STEALTH)
    page.goto("https://account.ycombinator.com/?continue=https%3A%2F%2Fwww.workatastartup.com%2Fcompanies", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(4000)
    # fill email
    page.fill("input[type=text]", EMAIL)
    page.wait_for_timeout(500)
    # magic link path
    page.click("a:has-text('magic link')", timeout=5000)
    page.wait_for_timeout(2000)
    print("URL:", page.url)
    body = page.inner_text("body")[:800]
    print("BODY:", body)
    page.screenshot(path="/tmp/yc_magic.png")
    ctx.close()
