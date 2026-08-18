#!/usr/bin/env python3
"""Final: click through consent (Continue) and capture Internshala cookies."""
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
        page.wait_for_timeout(2500)
        try:
            with page.expect_navigation(wait_until="domcontentloaded", timeout=15000):
                page.click("a:has-text('Login with Google')", timeout=10000)
        except Exception:
            pass
        if "accountchooser" in page.url:
            page.evaluate("""() => {
              const els = [...document.querySelectorAll('div[role=link], li[data-email], div[data-email]')];
              const t = els.find(e => (e.innerText||'').includes('@gmail.com'));
              if (t) t.click();
            }""")
            log("row clicked")
            page.wait_for_timeout(8000)

        # ride through: challenge (wait), consent/id (click Continue), landing
        for i in range(40):
            cur = page.url
            try:
                txt = page.inner_text("body")
            except Exception:
                txt = ""
            low = txt.lower()
            if cur.startswith("https://internshala.com") and "/login" not in cur:
                log("LANDED ON INTERNSHALA")
                break
            if "sign in to internshala" in low or ("/signin/oauth/id" in cur and "continue" in low):
                log("consent screen — clicking Continue")
                try:
                    page.click("button:has-text('Continue')", timeout=8000)
                    page.wait_for_timeout(12000)
                    continue
                except Exception as e:
                    log(f"continue click err: {e}")
            elif "challenge" in cur:
                log(f"challenge again — waiting ({i})")
                time.sleep(8)
                continue
            log(f"[{i}] {cur[:70]}")
            time.sleep(6)

        page.wait_for_timeout(5000)
        cookies = ctx.cookies()
        is_c = [c for c in cookies if "internshala.com" in (c.get("domain") or "")]
        page.screenshot(path="/tmp/is_landed_final.png")
        json.dump({"cookies": cookies, "url": page.url,
                   "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                  open(OUT, "w"), indent=1)
        log(f"cookies: total {len(cookies)}, internshala {len(is_c)}")
        log("final: " + page.url[:110])

if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
