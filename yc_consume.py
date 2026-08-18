#!/usr/bin/env python3
"""Consume the YC magic link: authenticate the yc_cap profile, land on
workatastartup.com, then save the session to portal_yc.json."""
import json, os, sys, time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
HERE = "/home/ubuntu/job_hunt_linkedin"
DST = os.path.join(HERE, "profiles", "yc_cap")
OUT = os.path.join(HERE, "portal_yc.json")
STEALTH = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
LINK = sys.argv[1]


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=DST, executable_path=CLOAK, headless=True,
        args=["--no-first-run", "--no-default-browser-check",
              "--disable-blink-features=AutomationControlled", "--window-size=1400,900"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.add_init_script(STEALTH)

    page.goto(LINK, wait_until="domcontentloaded", timeout=60000)
    log(f"after link: {page.url}")
    page.wait_for_timeout(6000)

    # ride redirects / possible consent
    for i in range(15):
        log(f"[{i}] {page.url[:100]}")
        if "workatastartup.com" in page.url and "authenticate" not in page.url and "account.ycombinator" not in page.url:
            break
        try:
            page.click("button:has-text('Continue'), button:has-text('Allow')", timeout=2000)
        except Exception:
            pass
        page.wait_for_timeout(4000)

    page.wait_for_timeout(4000)
    log(f"final: {page.url}")

    # verify logged-in: profile/avatar or "Log out" presence
    body = ""
    try:
        body = page.inner_text("body")[:500]
    except Exception:
        pass
    logged_in = ("log out" in body.lower()) or ("logout" in body.lower()) or ("profile" in body.lower() and "log in" not in body.lower())
    log(f"logged_in_heuristic={logged_in} body[:200]={body[:200]}")

    cookies = ctx.cookies()
    keep = [c for c in cookies if any(d in c.get("domain", "") for d in ("workatastartup", "ycombinator", "google"))]
    payload = {"cookies": cookies, "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
               "note": "yc session via magic link", "logged_in_heuristic": logged_in}
    tmp = OUT + ".tmp"
    json.dump(payload, open(tmp, "w"))
    os.replace(tmp, OUT)
    from collections import Counter
    log(f"saved {len(cookies)} cookies; key domains: {Counter(c.get('domain','?') for c in keep).most_common(10)}")
    ctx.close()
