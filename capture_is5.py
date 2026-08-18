#!/usr/bin/env python3
"""Relaunch flow and READ the tap-number challenge screen. Dumps text+screenshot."""
import json, os, sys, time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
HERE = "/home/ubuntu/job_hunt_linkedin"
DST = os.path.join(HERE, "profiles", "is_login")
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
            page.wait_for_timeout(6000)

        # read the challenge screen: dump text every 4s
        for i in range(45):
            cur = page.url
            try:
                txt = page.inner_text("body")
            except Exception:
                txt = ""
            log(f"[{i}] {cur[:60]}")
            if "challenge" in cur or "dp" in cur or "tap" in cur.lower() or "number" in txt.lower():
                page.screenshot(path="/tmp/is_number_screen.png")
                log("=== SCREEN TEXT START ===")
                log(txt[:800])
                log("=== SCREEN TEXT END ===")
            if cur.startswith("https://internshala.com") and "/login" not in cur:
                log("LANDED INTERNSHALA")
                break
            if "consent" in cur:
                for sel in ["button:has-text('Continue')", "button:has-text('Allow')"]:
                    try:
                        page.click(sel, timeout=5000)
                        log("consent clicked")
                        break
                    except Exception:
                        pass
            time.sleep(4)

        page.wait_for_timeout(3000)
        page.screenshot(path="/tmp/is_number_final.png")
        log("final url: " + page.url[:100])

if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
