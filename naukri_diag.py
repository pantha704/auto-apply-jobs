"""Naukri Access-Denied probe — which endpoints are reachable from this IP?"""
import os, sys, time, json
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
DST = "/home/ubuntu/job_hunt_linkedin/profiles/naukri_cap"

URLS = [
    "https://www.naukri.com/",
    "https://www.naukri.com/nlogin",
    "https://login.naukri.com/nLogin/Login",
    "https://www.naukri.com/mnjuser/homepage",
]

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=DST, executable_path=CLOAK, headless=False,
        args=["--no-first-run", "--no-default-browser-check",
              "--disable-blink-features=AutomationControlled", "--window-size=1280,720"])
    ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    pg.set_default_timeout(20000)
    for u in URLS:
        try:
            pg.goto(u, wait_until="domcontentloaded", timeout=35000)
            pg.wait_for_timeout(4000)
            body = pg.inner_text("body")[:220].replace("\n", " | ")
            title = pg.title()
            print(f"URL: {u}\n  final: {pg.url[:90]}\n  title: {title[:60]}\n  body: {body[:180]}\n  DENIED: {'access denied' in body.lower() or 'permission' in body.lower()}")
        except Exception as e:
            print(f"URL: {u}\n  ERR: {str(e)[:100]}")
        pg.screenshot(path=f"/tmp/naukri_probe_{URLS.index(u)}.png")
    ctx.close()
