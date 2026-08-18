import json, os, time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright
HERE = "/home/ubuntu/Documents/job_hunt_linkedin"
STATE = os.path.join(HERE, "portal_wellfound.json")
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=os.path.join(HERE, "profiles", "chk_" + str(int(time.time()%100000))), executable_path="/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome", headless=True,
        args=["--no-first-run","--disable-blink-features=AutomationControlled"])
    ctx.add_cookies(json.load(open(STATE)).get("cookies", []))
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    for g in range(3):
        try:
            page.goto("https://wellfound.com/profile/edit/overview", wait_until="domcontentloaded", timeout=40000)
            page.wait_for_timeout(5000); break
        except Exception: page.wait_for_timeout(3000)
    t = page.inner_text("body")
    print("profile complete:", "can't be found by recruiters" not in t, flush=True)
    i = t.find("What recruiters will see")
    print("VIEW:", t[i:i+500].replace(chr(10),' | ') if i > 0 else t[:300], flush=True)
    page.screenshot(path=os.path.join(HERE, "profiles", "overview.png"))
    ctx.close()
