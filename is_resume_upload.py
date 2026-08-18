#!/usr/bin/env python3
"""Upload resume to Internshala profile (the real resume slot, not the picture)."""
import json, os, sys, time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
HERE = "/home/ubuntu/job_hunt_linkedin"
DST = os.path.join(HERE, "profiles", "is_login")
RESUME = os.path.join(HERE, "resume_pratham.pdf")
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
        page.set_default_timeout(30000)

        # dashboard -> find resume section link
        page.goto("https://internshala.com/student/dashboard", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(5000)
        log("dashboard url: " + page.url)
        links = page.evaluate("""() => [...document.querySelectorAll('a')].filter(e => e.offsetParent !== null).map(e => ({t: (e.innerText||'').trim().slice(0,50), h: e.href})).filter(x => x.t && x.t.length < 55)""")
        seen = []
        for l in links:
            if any(k in l["t"].lower() for k in ["resume", "profile", "edit", "education"]) and l not in seen:
                seen.append(l)
        log("relevant links: " + json.dumps(seen, ensure_ascii=False))

        # try the resume page directly
        for url in ["https://internshala.com/student/resume", "https://internshala.com/student/edit_resume", "https://internshala.com/student/profile"]:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
                file_inputs = page.locator("input[type=file]").count()
                log(f"{url} -> {page.url} | file inputs: {file_inputs}")
                if file_inputs > 0:
                    page.locator("input[type=file]").first.set_input_files(RESUME, timeout=10000)
                    log("resume file set")
                    page.wait_for_timeout(3000)
                    for lab in ["Save", "Submit", "Upload", "Continue"]:
                        try:
                            b = page.locator(f"button:has-text('{lab}'), input[type=submit][value*='{lab}']").first
                            if b.is_visible(timeout=1500):
                                b.click(timeout=8000)
                                log(f"clicked {lab}")
                                break
                        except Exception:
                            continue
                    page.wait_for_timeout(4000)
                    page.screenshot(path="/tmp/is_resume_done.png")
                    log("after save: " + page.url)
                    # verify
                    txt = page.inner_text("body").lower()
                    log("resume mentioned on page: " + str("resume" in txt))
                    break
            except Exception as e:
                log(f"{url} err: {e}")

        page.screenshot(path="/tmp/is_resume_final.png")
        log("done: " + page.url)

if __name__ == "__main__":
    main()
