"""Read-only dump of the himalayas talent profile sections (no submits)."""
import os, sys, time, json
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
HERE = "/home/ubuntu/job_hunt_linkedin"
DST = os.path.join(HERE, "profiles", "hima_cap")
PORTAL = os.path.join(HERE, "portal_himalayas.json")


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=DST, executable_path=CLOAK, headless=True,
        args=["--no-first-run", "--no-default-browser-check",
              "--disable-blink-features=AutomationControlled", "--window-size=1280,720"])
    ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    try:
        ctx.add_cookies(json.load(open(PORTAL)).get("cookies", []))
    except Exception:
        pass
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    pg.set_default_timeout(15000)
    pg.goto("https://himalayas.app/profile/overview", wait_until="domcontentloaded", timeout=45000)
    pg.wait_for_timeout(5000)
    if "performing security verification" in pg.inner_text("body").lower():
        pg.mouse.click(212, 336)
        for _ in range(8):
            pg.wait_for_timeout(4000)
            if "performing security verification" not in pg.inner_text("body").lower():
                break
    log("url: " + pg.url)
    # collect profile nav links
    links = pg.evaluate("""() => [...document.querySelectorAll('a')].map(a => ({t: (a.innerText||'').trim().slice(0,40), h: a.href})).filter(x => x.h && x.h.includes('/profile/')).slice(0,20)""")
    for l in links:
        print(f"LINK: {l['t']} -> {l['h']}")
    # dump each section read-only
    for l in links:
        try:
            pg.goto(l["h"], wait_until="domcontentloaded", timeout=30000)
            pg.wait_for_timeout(3000)
            b = pg.inner_text("body")[:600]
            log(f"--- {l['h'].split('/profile/')[-1]} ---")
            log(b.replace("\n", " | "))
        except Exception as e:
            log(f"nav err {l['h'][:60]}: {str(e)[:60]}")
    pg.screenshot(path="/tmp/hima_profile_check.png")
    ctx.close()
