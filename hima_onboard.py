"""Himalayas onboarding — resume import, then dump the next wizard step."""
import os, sys, time, json
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
HERE = "/home/ubuntu/job_hunt_linkedin"
DST = os.path.join(HERE, "profiles", "hima_cap")
PORTAL = os.path.join(HERE, "portal_himalayas.json")
RESUME = os.path.join(HERE, "resume_pratham.pdf")


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=DST, executable_path=CLOAK, headless=False,
        args=["--no-first-run", "--no-default-browser-check",
              "--disable-blink-features=AutomationControlled", "--window-size=1280,720"])
    ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    try:
        ctx.add_cookies(json.load(open(PORTAL)).get("cookies", []))
    except Exception as e:
        log(f"cookie load err: {str(e)[:60]}")
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    pg.set_default_timeout(15000)
    pg.goto("https://himalayas.app/onboarding/talent/resume", wait_until="domcontentloaded", timeout=45000)
    pg.wait_for_timeout(5000)
    low = pg.inner_text("body").lower()
    if "performing security verification" in low or "verify you are human" in low:
        log("CF challenge — clicking checkbox pixel (212,336)")
        pg.mouse.click(212, 336)
        for _ in range(8):
            pg.wait_for_timeout(4000)
            if "performing security verification" not in pg.inner_text("body").lower():
                log("CF cleared")
                break
    log("url: " + pg.url)
    log("body: " + pg.inner_text("body")[:700].replace("\n", " | "))
    pg.screenshot(path="/tmp/hima_onb_1.png")

    try:
        fi = pg.locator("input[type=file]").first
        fi.set_input_files(RESUME)
        log("resume set via input[type=file]")
    except Exception as e:
        log(f"direct file input err: {str(e)[:80]} — trying dropzone click")
        try:
            pg.click("text=Click to upload or drag and drop", timeout=4000)
            pg.wait_for_timeout(1500)
            pg.locator("input[type=file]").first.set_input_files(RESUME)
            log("resume set after dropzone click")
        except Exception as e2:
            log(f"dropzone err: {str(e2)[:80]}")

    pg.wait_for_timeout(3000)
    # confirm the upload
    try:
        pg.click("button:has-text('Upload')", timeout=5000)
        log("clicked Upload")
    except Exception as e:
        log(f"upload click err: {str(e)[:80]}")
    # wait for resume parsing — URL or body change
    for _ in range(10):
        pg.wait_for_timeout(4000)
        u = pg.url
        b = pg.inner_text("body")[:400]
        if "/onboarding/talent/resume" not in u or "import your career details" not in b.lower():
            log(f"moved on: {u[:80]}")
            break
    log("post-upload url: " + pg.url)
    log("post-upload body: " + pg.inner_text("body")[:1000].replace("\n", " | "))
    pg.screenshot(path="/tmp/hima_onb_2.png")

    # upsell screen — take the free plan
    if "/plus" in pg.url or "continue with free plan" in pg.inner_text("body").lower():
        try:
            pg.click("button:has-text('Continue with free plan')", timeout=5000)
            log("clicked Continue with free plan")
        except Exception as e:
            log(f"free plan click err: {str(e)[:80]}")
        for _ in range(8):
            pg.wait_for_timeout(4000)
            if "/plus" not in pg.url:
                log(f"moved past plus: {pg.url[:80]}")
                break
        log("post-plus url: " + pg.url)
        log("post-plus body: " + pg.inner_text("body")[:1000].replace("\n", " | "))
        pg.screenshot(path="/tmp/hima_onb_3.png")
    ctx.close()
