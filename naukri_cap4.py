import os
#!/usr/bin/env python3
"""Naukri capture v4 — v3 + patient hydration. The spoofed-UA + banked-session combo
passes Akamai and lands on the logged-in dashboard skeleton; the page takes a while
to hydrate over the slow relay. Poll up to 90s for content, then decide:
logged-in markers -> re-export fresh session; login UI -> drive Google -> push."""
import json, os, sys, time
from playwright.sync_api import sync_playwright

CHROME = "/usr/bin/google-chrome-stable"
OUT = "/tmp/naukri_out.json"
EMAIL = os.environ.get("JOBHUNT_EMAIL", "")
PASSWORD = os.environ.get("GOOGLE_PASSWORD", "")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
SEC_CH_UA = '"Not_A Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"'

def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

banked = json.load(open("/tmp/naukri_bank.json"))["cookies"]
banked = [c for c in banked if "naukri" in (c.get("domain") or "")]

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir="/tmp/naukri_cap_v4", executable_path=CHROME, headless=True,
        user_agent=UA, locale="en-IN", timezone_id="Asia/Kolkata",
        args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
              "--window-size=1400,900", "--lang=en-US"])
    ctx.set_extra_http_headers({
        "sec-ch-ua": SEC_CH_UA, "sec-ch-ua-mobile": "?0", "sec-ch-ua-platform": '"Windows"',
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
    })
    ctx.add_cookies(banked)
    log(f"injected {len(banked)} banked naukri cookies (spoofed Windows-Chrome)")
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
    try:
        page.goto("https://www.naukri.com/mnjuser/homepage", wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        log(f"goto err: {str(e)[:100]}")
    # patient hydration: poll body until it has real content
    state = None
    for i in range(18):  # up to 90s
        page.wait_for_timeout(5000)
        try: body = page.inner_text("body")[:3000]
        except Exception: body = ""
        t = body.lower()
        if "access denied" in t or ("permission" in t and "server" in t):
            state = "blocked"; log("v4 blocked: " + body[:120].replace("\n"," | ")); break
        if "login with google" in t or "sign up" in t or "password" in t and "login" in page.url:
            state = "login"; log(f"v4 login-page at {i*5}s"); break
        if any(k in t for k in ("logout", "view profile", "edit profile", "my naukri", "jobseeker")):
            state = "logged_in"; log(f"v4 LOGGED-IN markers at {i*5}s"); break
        if i % 3 == 2:
            log(f"v4 hydrating... ({len(body)} chars body)")
    page.screenshot(path="/tmp/naukri_v4_state.png")
    log("v4 state: " + (state or "unknown"))

    if state == "logged_in":
        json.dump({"cookies": ctx.cookies(), "url": page.url,
                   "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, open(OUT, "w"))
        log("NAUKRI_OK " + OUT + f" | cookies: {len(ctx.cookies())}")
        ctx.close(); sys.exit(0)

    if state == "blocked":
        log("NAUKRI_FAIL blocked-by-akamai")
        ctx.close(); sys.exit(1)

    # login flow (state == 'login' or unknown)
    log("driving Google login")
    clicked = False
    for sel in ["a:has-text('Login With Google')", "a:has-text('Login with Google')",
                "button:has-text('Google')", "a[href*='google']"]:
        try:
            el = page.locator(sel).first
            if el.count() and el.is_visible():
                el.click(timeout=8000); clicked = True; log("clicked: " + sel); break
        except Exception as e:
            log(f"fail {sel}: {str(e)[:50]}")
    if not clicked:
        log("no google button found; switching to /mnjuser/login explicitly")
        try:
            page.goto("https://www.naukri.com/mnjuser/login", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(4000)
            for sel in ["a:has-text('Login With Google')", "a:has-text('Login with Google')", "a[href*='google']"]:
                el = page.locator(sel).first
                if el.count() and el.is_visible():
                    el.click(timeout=8000); clicked = True; log("clicked google on login page"); break
        except Exception as e:
            log("login-page nav err: " + str(e)[:70])
    page.wait_for_timeout(3000)

    deadline = time.time() + 360
    pushed = False
    while time.time() < deadline:
        u = page.url or ""
        try: body = page.inner_text("body")[:800]
        except Exception: body = ""
        if any(k in u for k in ("challenge", "/dp?", "recovery", "piv")) or "approve" in body.lower() or "is it you" in body.lower():
            if not pushed:
                pushed = True; log("2FA_PUSH_NEEDED — LO ACCEPT THE GOOGLE PUSH NOW")
                page.screenshot(path="/tmp/naukri_v4_2fa.png")
        if "consent" in u:
            for label in ["Continue", "Allow", "Weiter", "Zulassen"]:
                try:
                    b = page.locator(f"button:has-text('{label}')").first
                    if b.count() and b.is_visible():
                        b.click(force=True, timeout=5000); log("consent: " + label); break
                except Exception: pass
            time.sleep(4)
        if "naukri" in u and "/login" not in u:
            try:
                if any(k in page.inner_text("body").lower() for k in ("logout", "view profile", "edit profile", "my naukri")):
                    log("LOGGED IN after google: " + u[:90]); page.wait_for_timeout(5000); break
            except Exception: pass
        try:
            el = page.locator("input#identifierId").first
            if el.count() and el.is_visible() and not el.input_value():
                el.fill(EMAIL); page.keyboard.press("Enter"); log("email filled"); time.sleep(3); continue
        except Exception: pass
        try:
            pw = page.locator("input[type=password]").first
            if pw.count() and pw.is_visible() and not pw.input_value() and "accounts.google.com" in u:
                pw.fill(PASSWORD); page.keyboard.press("Enter"); log("password filled"); time.sleep(3); continue
        except Exception: pass
        time.sleep(5)

    cookies = ctx.cookies()
    nauk = [c for c in cookies if "naukri.com" in (c.get("domain") or "")]
    log(f"final: {page.url[:90]} | naukri cookies: {len(nauk)}")
    page.screenshot(path="/tmp/naukri_v4_final.png")
    if nauk and ("/login" not in page.url):
        json.dump({"cookies": cookies, "url": page.url,
                   "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, open(OUT, "w"))
        log("NAUKRI_OK " + OUT)
    else:
        log("NAUKRI_FAIL " + page.url[:90])
    ctx.close()