"""Manual SSO driver for himalayas.app — rich diagnostics, screenshots at every state."""
import os, sys, time, json
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
HERE = "/home/ubuntu/job_hunt_linkedin"
DST = os.path.join(HERE, "profiles", "hima_cap")
OUT = os.path.join(HERE, "portal_himalayas.json")
PASSWORD = os.environ.get("GOOGLE_PASSWORD", "")
WAIT_SEC = int(os.environ.get("WAIT_SEC", "300"))


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def body(page):
    try:
        return page.inner_text("body")[:600]
    except Exception:
        return ""


with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=DST, executable_path=CLOAK, headless=False,
        args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
              "--window-size=1280,720"])
    ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    try:
        ctx.add_cookies(json.load(open(OUT)).get("cookies", []))
    except Exception:
        pass
    mp = ctx.pages[0] if ctx.pages else ctx.new_page()
    mp.set_default_timeout(15000)
    mp.goto("https://himalayas.app/signup/talent", wait_until="domcontentloaded", timeout=45000)
    mp.wait_for_timeout(5000)
    mb = body(mp).lower()
    if "performing security verification" in mb or "verify you are human" in mb:
        mp.mouse.click(212, 336)
        log("CF clicked")
        for _ in range(8):
            mp.wait_for_timeout(4000)
            if "performing security verification" not in body(mp).lower():
                break
    log(f"main page: {mp.url[:70]} | {body(mp)[:100]}")

    # click sign up with google, capture popup
    try:
        with ctx.expect_page(timeout=10000) as pop:
            mp.click("button:has-text('Sign up with Google')", timeout=8000)
        gp = pop.value
        log("popup opened")
    except Exception as e:
        log(f"popup click err: {str(e)[:80]}")
        gp = None
        for pg in ctx.pages:
            if "google.com" in urlparse(pg.url).netloc:
                gp = pg
                log("found google page among existing pages")

    t0 = time.time()
    last_url = None
    while time.time() - t0 < WAIT_SEC:
        if gp is None:
            for pg in ctx.pages:
                if "google.com" in urlparse(pg.url).netloc:
                    gp = pg
            time.sleep(3)
            continue
        cur = gp.url
        if cur != last_url:
            last_url = cur
            log(f"google url: {cur[:100]}")
        gb = body(gp)
        low = gb.lower()

        if "is it you trying to sign in" in low or ("tap yes" in low and "phone" in low):
            log("*** PHONE PROMPT — waiting for LO's tap ***")
            for _ in range(75):
                time.sleep(4)
                n = gp.url
                if n != cur:
                    log(f"google moved: {n[:80]}")
                    break
                if "google.com" not in urlparse(n).netloc:
                    break
            continue

        if "challenge/pwd" in cur or "enter your password" in low:
            try:
                pw = gp.locator("input[type=password]").first
                pw.fill(PASSWORD)
                gp.keyboard.press("Enter")
                log("password submitted")
                time.sleep(5)
                continue
            except Exception as e:
                log(f"password fill err: {str(e)[:60]}")
            try:
                gp.click("text=Try another way", timeout=3000)
                log("clicked Try another way")
                time.sleep(4)
                continue
            except Exception:
                pass

        if ("will allow" in low and "access this info" in low) or "consent" in cur.lower():
            gp.screenshot(path="/tmp/hima_consent.png")
            for label in ("Continue", "Allow"):
                try:
                    gp.click(f"button:has-text('{label}')", timeout=2500)
                    log(f"consent: clicked {label}")
                    time.sleep(5)
                    break
                except Exception:
                    continue
            continue

        if "Try another way" in gb:
            try:
                gp.click("text=Try another way", timeout=3000)
                log("clicked Try another way")
                time.sleep(4)
                continue
            except Exception:
                pass

        # chooser: click the account by rendered text
        try:
            el = gp.locator("" + os.environ.get("JOBHUNT_EMAIL","") + "").first
            if el.count() > 0:
                el.click(timeout=4000)
                log("clicked account email text")
                time.sleep(5)
                continue
        except Exception as e:
            log(f"email text click err: {str(e)[:60]}")
        try:
            el2 = gp.locator("text=Pratham Jaiswal").first
            if el2.count() > 0:
                el2.click(timeout=4000)
                log("clicked account name text")
                time.sleep(5)
                continue
        except Exception as e:
            log(f"name text click err: {str(e)[:60]}")
        log(f"no handle matched | url={cur[:60]} | body={gb[:120].replace(chr(10),' ')}")
        time.sleep(5)

    # final: check main page state
    for pg in ctx.pages:
        u = pg.url
        if "himalayas.app" in urlparse(u).netloc:
            bb = body(pg)
            low = bb.lower()
            log(f"FINAL himalayas page: {u[:80]}")
            log(f"body: {bb[:200]}")
            if "sign up" not in low and "sign in" not in low:
                cookies = ctx.cookies()
                json.dump({"cookies": cookies, "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                           "note": "himalayas google sso session"}, open(OUT, "w"))
                log(f"*** AUTH OK — SAVED {len(cookies)} cookies ***")
            pg.screenshot(path="/tmp/hima_final.png")
    ctx.close()
