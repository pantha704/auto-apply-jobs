#!/usr/bin/env python3
"""Patient version: click row once, WAIT for user's Google 2FA approval (no re-clicking)."""
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
        except Exception as e:
            log(f"nav: {e}")

        # chooser -> click the account row exactly once
        if "accountchooser" in page.url:
            r = page.evaluate("""() => {
              const els = [...document.querySelectorAll('div[role=link], li[data-email], div[data-email]')];
              const t = els.find(e => (e.innerText||'').includes('@gmail.com'));
              if (t) { t.click(); return 'row-clicked'; }
              return 'no-row';
            }""")
            log(f"chooser: {r}")
            page.wait_for_timeout(6000)

        # patient loop: NEVER click while on challenge; wait for user's phone approval
        deadline = time.time() + 300
        while time.time() < deadline:
            cur = page.url
            log(f"url: {cur[:75]}")
            if cur.startswith("https://internshala.com") and "/login" not in cur:
                log("LANDED ON INTERNSHALA")
                break
            if "consent" in cur:
                for sel in ["button:has-text('Continue')", "button:has-text('Allow')", "button[type=submit]", "input[type=submit]"]:
                    try:
                        page.click(sel, timeout=6000)
                        log(f"consent clicked: {sel}")
                        break
                    except Exception:
                        continue
                page.wait_for_timeout(12000)
            elif "challenge" in cur or "dp?" in cur or "piv" in cur or "prompt" in cur:
                log("2FA challenge on screen — waiting for phone approval (no clicking)")
                time.sleep(10)
                continue
            else:
                time.sleep(8)

        page.wait_for_timeout(4000)
        cookies = ctx.cookies()
        is_c = [c for c in cookies if "internshala.com" in (c.get("domain") or "")]
        log(f"cookies: total {len(cookies)}, internshala {len(is_c)}")
        page.screenshot(path="/tmp/is_landed.png")
        json.dump({"cookies": cookies, "url": page.url,
                   "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                  open(OUT, "w"), indent=1)
        log("final: " + page.url[:110])

if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
