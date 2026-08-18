"""ONE-SHOT SSO attempt — zero retry loops, zero spam.

State machine: chooser → click account ONCE → password ONCE → passive wait for
2FA tap → consent ONCE → verify. Unknown states are reported, never clicked.
"""
import os, sys, time, json
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
HERE = "/home/ubuntu/job_hunt_linkedin"
DST = os.path.join(HERE, "profiles", "hima_cap")
OUT = os.path.join(HERE, "portal_himalayas.json")
PASSWORD = os.environ.get("GOOGLE_PASSWORD", "")


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def snap(pg, name):
    p = f"/tmp/hima_{name}.png"
    try:
        pg.screenshot(path=p)
        log(f"shot: {p}")
    except Exception as e:
        log(f"shot err: {str(e)[:60]}")


def body(pg):
    try:
        return pg.inner_text("body")[:800]
    except Exception:
        return ""


def passive_wait(pg, seconds, label):
    """No clicks. Just watch for URL changes / prompt text. Returns final body."""
    t0 = time.time()
    last = pg.url
    while time.time() - t0 < seconds:
        time.sleep(5)
        n = pg.url
        if n != last:
            log(f"{label}: url changed to {n[:90]}")
            last = n
        b = body(pg).lower()
        if "is it you trying to sign in" in b or ("tap yes" in b and "phone" in b):
            log(f"{label}: phone prompt visible — keep waiting for LO's tap")
    return body(pg)


with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=DST, executable_path=CLOAK, headless=True,
        args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
              "--window-size=1280,720"])
    ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    try:
        ctx.add_cookies(json.load(open(OUT)).get("cookies", []))
    except Exception:
        pass
    mp = ctx.pages[0] if ctx.pages else ctx.new_page()
    mp.set_default_timeout(12000)
    mp.goto("https://himalayas.app/signup/talent", wait_until="domcontentloaded", timeout=45000)
    mp.wait_for_timeout(5000)
    low = body(mp).lower()
    log(f"main: {mp.url[:70]} | {body(mp)[:80]}")
    if "performing security verification" in low:
        mp.mouse.click(212, 336)
        log("CF: clicked checkbox once")
        for _ in range(8):
            mp.wait_for_timeout(4000)
            if "performing security verification" not in body(mp).lower():
                log("CF: cleared")
                break

    try:
        mp.click("button:has-text('Sign up with Google')", timeout=8000)
        log("clicked Sign up with Google")
    except Exception as e:
        log(f"google button click err: {str(e)[:60]}")
        snap(mp, "main_after_click_fail")
        ctx.close()
        sys.exit(1)
    mp.wait_for_timeout(6000)

    gp = None
    for pg in ctx.pages:
        h = urlparse(pg.url).netloc
        log(f"page: {pg.url[:90]}")
        if "google.com" in h:
            gp = pg
    if gp is None:
        log("NO google page found after click")
        snap(mp, "no_google_page")
        ctx.close()
        sys.exit(2)

    gb = body(gp).lower()
    cur = gp.url
    log(f"google state: {cur[:80]}")
    log(f"google body: {body(gp)[:350]}")
    snap(gp, "state_1_initial")

    # --- STATE 1: chooser — click the account ONCE ---
    if "choose an account" in gb and "@gmail.com" in gb:
        clicked = False
        for sel in (f"text={os.environ.get('JOBHUNT_EMAIL','')}", f"text={os.environ.get('JOBHUNT_NAME','')}"):
            try:
                el = gp.locator(sel).first
                if el.count() > 0:
                    el.click(timeout=4000)
                    log(f"chooser: clicked account via {sel!r}")
                    clicked = True
                    break
            except Exception as e:
                log(f"click {sel!r} err: {str(e)[:60]}")
        if not clicked:
            log("chooser: could not click account row — reporting, not retrying")
            ctx.close()
            sys.exit(3)
        time.sleep(6)
        gb = body(gp).lower()
        cur = gp.url
        log(f"after account click: {cur[:80]}")
        log(f"body: {body(gp)[:300]}")
        snap(gp, "state_2_after_account")

    # --- STATE 2: password — fill ONCE ---
    if "challenge/pwd" in cur or "enter your password" in gb:
        try:
            gp.locator("input[type=password]").first.fill(PASSWORD)
            gp.keyboard.press("Enter")
            log("password: filled + submitted ONCE")
            time.sleep(6)
            gb = body(gp).lower()
            cur = gp.url
            log(f"after password: {cur[:80]}")
            log(f"body: {body(gp)[:300]}")
            snap(gp, "state_3_after_password")
        except Exception as e:
            log(f"password err: {str(e)[:60]} — reporting, not retrying")

    # --- STATE 3: passive wait for 2FA prompt / redirect (no clicks) ---
    gb = passive_wait(gp, 120, "2fa-wait")
    cur = gp.url
    if "is it you trying to sign in" in gb or "tap yes" in gb:
        log("*** PHONE PROMPT — waiting passively up to 4 more minutes for LO's tap ***")
        gb = passive_wait(gp, 240, "tap-wait")
        cur = gp.url
        log(f"after tap-wait: {cur[:80]} | {body(gp)[:200]}")

    # --- STATE 4: consent — click ONCE ---
    if ("will allow" in gb and "access this info" in gb) or "consent" in cur.lower():
        try:
            gp.click("button:has-text('Continue')", timeout=4000)
            log("consent: clicked Continue ONCE")
            time.sleep(6)
            snap(gp, "state_4_after_consent")
        except Exception as e:
            log(f"consent click err: {str(e)[:60]}")

    # --- FINAL: verify himalayas auth ---
    authed = False
    for pg in ctx.pages:
        if "himalayas.app" in urlparse(pg.url).netloc:
            bb = body(pg)
            lowb = bb.lower()
            log(f"final himalayas page: {pg.url[:80]}")
            log(f"body: {bb[:250]}")
            snap(pg, "final_himalayas")
            if "sign up" not in lowb and "sign in" not in lowb and "performing security" not in lowb:
                cookies = ctx.cookies()
                json.dump({"cookies": cookies, "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                           "note": "himalayas google sso session"}, open(OUT, "w"))
                log(f"*** AUTH OK — SAVED {len(cookies)} cookies ***")
                authed = True
    log(f"DONE authed={authed} google_now={gp.url[:80]}")
    ctx.close()
