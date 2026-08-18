#!/usr/bin/env python3
"""Test: launch Chrome with user's LinkedIn profile, verify session."""
from playwright.sync_api import sync_playwright
import sys

PROFILE = "/home/ubuntu/.config/google-chrome/Profile 4"

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=PROFILE,
        channel="chrome",
        headless=False,
        args=["--no-first-run", "--no-default-browser-check", "--disable-sync",
              "--disable-features=Translate", "--window-size=1400,900"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(4000)
    print("URL:", page.url)
    print("TITLE:", page.title())
    cookies = ctx.cookies()
    li = [c for c in cookies if "li_at" in c.get("name", "")]
    js = [c for c in cookies if c.get("name") == "JSESSIONID"]
    print("li_at:", "YES" if li else "NO", "| JSESSIONID:", "YES" if js else "NO")
    page.screenshot(path="/tmp/li_test.png")
    print("screenshot: /tmp/li_test.png")
    ctx.close()
