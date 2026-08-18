#!/usr/bin/env python3
"""Himalayas Google SSO — popup-aware waiter.

The Google account chooser opens in a POPUP page while the main page stays on
himalayas.app. This script monitors ALL pages, picks the pratham account in
the popup, triggers the phone prompt, and waits for the tap. Success = the
main page leaves the signup/chooser state (onboarding or redirect).
"""
import os, sys, time, json
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
HERE = "/home/ubuntu/job_hunt_linkedin"
DST = os.path.join(HERE, "profiles", "hima_cap")
OUT = os.path.join(HERE, "portal_himalayas.json")
STEALTH = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
WAIT_SEC = int(os.environ.get("WAIT_SEC", "240"))
HEADED = os.environ.get("HEADED") == "1"
PASSWORD = os.environ.get("GOOGLE_PASSWORD", "")  # Google account password via env


def host_of(u):
    try:
        return urlparse(u).netloc.lower()
    except Exception:
        return ""


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def body_of(page):
    try:
        return page.inner_text("body")[:900]
    except Exception:
        return ""


def dump_state(ctx, phase, note=""):
    """Share current position with a reviewer so it can take over the SAME session."""
    qdir = os.path.join(HERE, "state_queue")
    os.makedirs(qdir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    shot = os.path.join(qdir, f"hima_{ts}.png")
    pages = []
    for pg in ctx.pages:
        try:
            pages.append({"url": pg.url[:300], "body": body_of(pg)[:1500]})
            if not os.path.exists(shot):
                pg.screenshot(path=shot)
        except Exception:
            pass
    payload = {
        "script": "hima_sso_wait.py",
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "phase": phase,
        "note": note,
        "profile_dir": DST,
        "screenshot": shot,
        "pages": pages,
    }
    out = os.path.join(qdir, f"hima_{ts}.json")
    json.dump(payload, open(out, "w"))
    log(f"STATE DUMPED: {out}")
    return out


def main_page(ctx):
    for pg in ctx.pages:
        try:
            if host_of(pg.url).endswith("himalayas.app"):
                return pg
        except Exception:
            pass
    return None


def google_page(ctx):
    for pg in ctx.pages:
        try:
            if "google.com" in host_of(pg.url):
                return pg
        except Exception:
            pass
    return None


with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=DST, executable_path=CLOAK, headless=not HEADED,
        args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
              "--window-size=1280,720"])
    # stealth on any future page
    ctx.add_init_script(STEALTH)
    try:
        ctx.add_cookies(json.load(open(OUT)).get("cookies", []))
    except Exception:
        pass
    mp = ctx.pages[0] if ctx.pages else ctx.new_page()
    mp.set_default_timeout(20000)
    mp.goto("https://himalayas.app/signup/talent", wait_until="domcontentloaded", timeout=45000)
    mp.wait_for_timeout(5000)
    try:
        mp.click("button:has-text('Sign up with Google')", timeout=8000)
        log("google clicked")
        last_progress = time.time()
    except Exception as e:
        log(f"click err: {str(e)[:60]} — will retry in loop (CF challenge or slow render)")
        last_progress = time.time()

    t0 = time.time()
    prompted = False
    last_gp_url = None
    last_click_try = 0.0
    while time.time() - t0 < WAIT_SEC:
        if time.time() - last_progress > 120:
            log("no state change for 120s — dumping for triage")
            dump_state(ctx, "stalled", "no state change for 120s")
            ctx.close()
            sys.exit(3)
        gp = google_page(ctx)
        mpg = main_page(ctx)
        if gp is not None:
            gu = gp.url
            if gu != last_gp_url:
                last_gp_url = gu
                last_progress = time.time()
        if mpg is not None:
            mbody = body_of(mpg).lower()
            # Cloudflare challenge — solve Turnstile
            if "performing security verification" in mbody or "just a moment" in mbody or "verify you are human" in mbody:
                try:
                    mpg.mouse.click(212, 336)  # proven Turnstile checkbox pixel at 1280x720
                    log("CF checkbox clicked at (212,336)")
                    last_progress = time.time()
                    time.sleep(6)
                    continue
                except Exception as e:
                    log(f"CF click attempt failed: {str(e)[:60]}")
            # signup page rendered — (re)click the Google button, throttled
            elif "sign up with google" in mbody and gp is None and time.time() - last_click_try > 15:
                last_click_try = time.time()
                try:
                    mp.click("button:has-text('Sign up with Google')", timeout=5000)
                    log("google clicked (loop retry)")
                    last_progress = time.time()
                    time.sleep(4)
                    continue
                except Exception as e:
                    log(f"signup click retry failed: {str(e)[:60]}")
        # success: main page moved past the chooser AND shows auth state (never a mid-redirect race)
        if mpg and "himalayas.app" in mpg.url:
            mb = body_of(mpg)
            if "Choose an account" not in mb and "Sign up with Google" not in mb:
                if "/signup" not in mpg.url:
                    log(f"SUCCESS — main page moved on: {mpg.url[:70]}")
                    break
                if "Get started" in mb or "onboarding" in mpg.url or ("profile" in mpg.url.lower() and "Choose an account" not in mb):
                    log("SUCCESS — onboarding state")
                    break
        # drive the google popup
        if gp:
            gb = body_of(gp)
            log(f"google popup: {gp.url[:70]} | {gb[:200].replace(chr(10),' ')}")
            if "is it you trying to sign in" in gb.lower() or (("yes" in gb.lower() and "no" in gb.lower()) and "sign in" in gb.lower()):
                if not prompted:
                    log("PROMPT SENT — tap Yes on your phone!")
                    prompted = True
            elif ("will allow" in gb.lower() and "access this info" in gb.lower()) or "consent" in gp.url.lower():
                for label in ("Continue", "Allow", "Weiter", "Zulassen"):
                    try:
                        gp.click(f"button:has-text('{label}')", timeout=2000)
                        log(f"consent screen — clicked {label}")
                        time.sleep(4)
                        break
                    except Exception:
                        continue
                continue
            elif "challenge/pwd" in gp.url or "enter your password" in gb.lower() or ("password" in gb.lower() and "to continue" in gb.lower()):
                # LO gave us the Google password before — fill it first, fall back to "Try another way"
                try:
                    pw = gp.locator("input[type=password]").first
                    if pw.count() > 0:
                        pw.fill(PASSWORD)
                        gp.keyboard.press("Enter")
                        log("password screen — filled and submitted")
                        time.sleep(5)
                        continue
                except Exception:
                    log("password fill failed — falling back to Try another way")
                try:
                    gp.click("text=Try another way", timeout=3000)
                    log("password screen — clicked Try another way")
                    time.sleep(4)
                    continue
                except Exception:
                    log("password screen — no Try another way link")
            elif "Try another way" in gb:
                try:
                    gp.click("text=Try another way", timeout=3000)
                    log("clicked Try another way")
                    time.sleep(4)
                    continue
                except Exception:
                    pass
            else:
                try:
                    r = gp.evaluate("""() => {
                      const els = [...document.querySelectorAll('div[role=link], li[data-email], div[data-email]')];
                      const t = els.find(e => (e.innerText||'').toLowerCase().includes('pratham'));
                      if (t) { t.click(); return true; } return false;
                    }""")
                    if r:
                        log("account picked in popup")
                except Exception:
                    pass
        time.sleep(4)

    # final state
    mpg = main_page(ctx)
    gp = google_page(ctx)
    if gp is None and mpg is not None:
        mb = body_of(mpg)
        if "Choose an account" not in mb and "Sign up with Google" not in mb:
            log("AUTH OK — saving cookies")
            cookies = ctx.cookies()
            json.dump({"cookies": cookies, "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "note": "himalayas google sso session"}, open(OUT, "w"))
            log(f"SAVED {len(cookies)} cookies; body: {mb[:150]}")
            mpg.screenshot(path="/tmp/hima_done.png")
        else:
            log("popup closed but signup not complete — still chooser state")
            dump_state(ctx, "final-chooser", "timeout without auth")
    else:
        log(f"ended: main={mpg.url[:60] if mpg else None} google={'yes' if gp else 'no'} prompted={prompted}")
        dump_state(ctx, "timeout", f"WAIT_SEC={WAIT_SEC} elapsed, prompted={prompted}")
    ctx.close()
