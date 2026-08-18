#!/usr/bin/env python3
"""Focused: click the account row, ride consent, land Internshala, save cookies."""
import json, os, sys, time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
HERE = "/home/ubuntu/job_hunt_linkedin"
DST = os.path.join(HERE, "profiles", "is_login")
OUT = os.path.join(HERE, "portal_internshala.json")
STEALTH = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=DST, executable_path=CLOAK, headless=True,
            args=["--no-first-run", "--no-default-browser-check",
                  "--disable-blink-features=AutomationControlled", "--window-size=1400,900"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.add_init_script(STEALTH)

        page.goto("https://internshala.com/login/user", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)
        try:
            with page.expect_navigation(wait_until="domcontentloaded", timeout=15000):
                page.click("a:has-text('Login with Google')", timeout=10000)
            log("navigated")
        except Exception as e:
            log(f"nav: {e}")

        # click the account row — JS click on the element containing the email
        for attempt in range(4):
            cur = page.url
            log(f"[{attempt}] {cur[:70]}")
            if cur.startswith("https://internshala.com") and "/login" not in cur:
                log("LANDED ON INTERNSHALA")
                break
            if "accounts.google.com" in cur:
                clicked = page.evaluate("""() => {
                  const els = [...document.querySelectorAll('div[role=link], li[data-email], div[data-email], a[href*="accountchooser"]')];
                  const t = els.find(e => (e.innerText||'').includes('@gmail.com'));
                  if (t) { t.click(); return 'row-clicked'; }
                  const anyel = els.find(e => (e.innerText||'').includes('@gmail.com'));
                  if (anyel) { anyel.click(); return 'alt-row'; }
                  return 'no-row';
                }""")
                log(f"click result: {clicked}")
                page.wait_for_timeout(12000)
                cur = page.url
                log(f"after click: {cur[:80]}")
                if "consent" in cur:
                    for sel in ["button:has-text('Continue')", "button:has-text('Allow')", "button[type=submit]", "input[type=submit]"]:
                        try:
                            page.click(sel, timeout=6000)
                            log(f"consent clicked: {sel}")
                            break
                        except Exception:
                            continue
                    page.wait_for_timeout(12000)
                    log(f"after consent: {page.url[:80]}")
                if cur.startswith("https://internshala.com") and "/login" not in cur:
                    log("LOGGED IN")
                    break
            time.sleep(5)

        page.wait_for_timeout(5000)
        cookies = ctx.cookies()
        is_c = [c for c in cookies if "internshala.com" in (c.get("domain") or "")]
        log(f"cookies: total {len(cookies)}, internshala {len(is_c)}")
        page.screenshot(path="/tmp/is_landed.png")
        json.dump({"cookies": cookies, "url": page.url,
                   "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                  open(OUT, "w"), indent=1)
        log("final url: " + page.url[:110])

if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
