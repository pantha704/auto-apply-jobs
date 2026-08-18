import json, os, time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright
HERE = "/home/ubuntu/Documents/job_hunt_linkedin"
STATE = os.path.join(HERE, "portal_wellfound.json")
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=os.path.join(HERE, "profiles", "sess_chk"), executable_path="/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome", headless=True,
        args=["--no-first-run","--disable-blink-features=AutomationControlled"])
    ctx.add_cookies(json.load(open(STATE)).get("cookies", []))
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://wellfound.com/", wait_until="domcontentloaded", timeout=40000)
    page.wait_for_timeout(4000)
    t = page.inner_text("body")
    logged = "Open to offers" in t or "Interview booked" in t or "Find jobs" in t.lower()
    print("SESSION OK, logged in:", logged, flush=True)
    if logged:
        json.dump({"cookies": ctx.cookies()}, open(STATE, "w"))
        print("re-saved fresh session:", len(ctx.cookies()), "cookies", flush=True)
    ctx.close()
