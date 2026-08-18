#!/usr/bin/env python3
"""gmail_login.py — capture a Gmail-scoped Google session for the YC magic-link reader.
Rides the already-warm li_login_profile: goto mail.google.com -> account row ->
consent -> (possibly a push LO taps) -> Gmail loads -> export session cookies.
Writes portal_gmail.json with the Gmail/Google-scope cookies."""
import os, json, shutil, time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
HERE = "/home/ubuntu/job_hunt_linkedin"
SRC = os.path.join(HERE, "profiles", "li_login_profile")
DST = os.path.join(HERE, "profiles", "gmail_cap")
OUT = os.path.join(HERE, "portal_gmail.json")
EMAIL = os.environ.get("JOBHUNT_EMAIL", "")
PASSWORD = os.environ.get("GOOGLE_PASSWORD", "")
STEALTH = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

if os.path.exists(DST):
    shutil.rmtree(DST)
shutil.copytree(SRC, DST, ignore=shutil.ignore_patterns("LOCK", "Singleton*", "Crashpad", "Crash Reports"))
log("profile copied to gmail_cap")

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=DST, executable_path=CLOAK, headless=True,
        args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
              "--window-size=1400,900"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.add_init_script(STEALTH)
    try:
        page.goto("https://accounts.google.com/AccountChooser?continue=https://mail.google.com/mail/u/0/&service=mail",
                  wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
    except Exception as e:
        log("goto err: " + str(e)[:100])
    log("chooser url: " + page.url[:110])

    deadline = time.time() + 240
    pushed = False
    done = False
    shot_i = 0
    while time.time() < deadline and not done:
        shot_i += 1
        if shot_i <= 12:
            try: page.screenshot(path=f"/tmp/gml_{shot_i:02d}.png")
            except Exception: pass
        u = page.url or ""
        try: body = page.inner_text("body")[:700]
        except Exception: body = ""
        # account row click (chooser)
        try:
            r = page.evaluate("""() => {
              const els = [...document.querySelectorAll('div[role=link], li[data-email], div[data-email], [data-identifier]')];
              const t = els.find(e => (e.innerText||'').toLowerCase().includes('pratham'));
              if (t) { t.click(); return true; }
              return false;
            }""")
            if r: log("account row clicked (pratham)")
        except Exception: pass
        # consent / continue
        if "consent" in u or "accounts.google.com" in u and ("mail.google" in body or "permission" in body.lower()):
            for label in ["Continue", "Allow", "Weiter", "Zulassen", "Accept"]:
                try:
                    b = page.locator(f"button:has-text('{label}')").first
                    if b.count() and b.is_visible():
                        b.click(force=True, timeout=5000); log("consent: " + label); break
                except Exception: pass
        # 2FA push
        if any(k in u for k in ("challenge", "/dp?", "recovery", "piv")) or "is it you" in body.lower() or "approve" in body.lower():
            if not pushed:
                pushed = True
                log("2FA_PUSH_NEEDED — TAP THE GOOGLE PUSH ON YOUR PHONE NOW")
                page.screenshot(path="/tmp/gmail_login_2fa.png")
        # password fill
        try:
            pw = page.locator("input[type=password]").first
            if pw.count() and pw.is_visible() and not pw.input_value() and "accounts.google.com" in u:
                pw.fill(PASSWORD); page.keyboard.press("Enter"); log("password filled"); time.sleep(3); continue
        except Exception: pass
        # email fill (only if needed)
        try:
            el = page.locator("input#identifierId").first
            if el.count() and el.is_visible() and not el.input_value():
                el.fill(EMAIL); page.keyboard.press("Enter"); log("email filled"); time.sleep(3); continue
        except Exception: pass
        # Gmail loaded = success
        if "mail.google.com" in u and "signin" not in u:
            page.wait_for_timeout(6000)
            try:
                b2 = page.inner_text("body")[:300]
                if "Compose" in b2:
                    log("GMAIL LOADED — logged in!")
                    done = True
                    break
            except Exception: pass
        time.sleep(4)

    page.screenshot(path="/tmp/gmail_login_final.png")
    cookies = ctx.cookies()
    gmail = [c for c in cookies if any(d in (c.get("domain") or "") for d in ("google.com",))]
    log(f"final url: {page.url[:100]} | google cookies: {len(gmail)}")
    if done:
        json.dump({"cookies": cookies, "url": page.url,
                   "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, open(OUT, "w"))
        log("GMAIL_OK " + OUT)
    else:
        log("GMAIL_FAIL " + page.url[:100])
    ctx.close()