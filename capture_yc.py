#!/usr/bin/env python3
"""Capture workatastartup.com session via the known-good Google SSO profile.

Reuses the Wellfound worker profile (valid Google account session, proven
silent re-auth) on a COPY so the live workers are never disturbed.
Writes portal_yc.json with workatastartup.com auth cookies.
"""
import json, os, shutil, sys, time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
HERE = "/home/ubuntu/job_hunt_linkedin"
SRC_PROFILE = os.path.join(HERE, "profiles", "wf_w_wf-w1")
DST = os.path.join(HERE, "profiles", "yc_cap")
OUT = os.path.join(HERE, "portal_yc.json")
STEALTH = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    # fresh copy of the SSO profile (never touch the live worker's)
    if os.path.exists(DST):
        shutil.rmtree(DST)
    shutil.copytree(SRC_PROFILE, DST, ignore=shutil.ignore_patterns("LOCK", "Singleton*", "Crashpad", "Crash Reports"))
    log(f"profile copied: {SRC_PROFILE} -> {DST}")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=DST, executable_path=CLOAK, headless=True,
            args=["--no-first-run", "--no-default-browser-check",
                  "--disable-blink-features=AutomationControlled", "--window-size=1400,900"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.add_init_script(STEALTH)

        page.goto("https://www.workatastartup.com/companies", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)
        log(f"landed: {page.url}")

        # find and click login (button or link)
        clicked = False
        for sel in ["text=Log in", "text=Sign in", "a[href*='login']", "button:has-text('Log in')"]:
            try:
                page.click(sel, timeout=4000)
                clicked = True
                log(f"clicked login via {sel}")
                break
            except Exception:
                continue
        if not clicked:
            log("no login element found — maybe already logged in?")
        page.wait_for_timeout(5000)

        # ride through Google OAuth: account chooser / consent / redirect back
        for i in range(30):
            cur = page.url
            log(f"[{i}] url={cur[:90]}")
            if "workatastartup.com" in cur and "login" not in cur and "auth" not in cur:
                # back on the site — check if logged in (avatar/user menu present)
                try:
                    body = page.inner_text("body")[:400]
                    if "Log in" not in body or "Sign in" in body.lower():
                        log("landed back on site")
                        break
                except Exception:
                    pass
            try:
                # account chooser: pick our account
                r = page.evaluate("""() => {
                  const els = [...document.querySelectorAll('div[role=link], li[data-email], div[data-email]')];
                  const t = els.find(e => (e.innerText||'').toLowerCase().includes('pratham'));
                  if (t) { t.click(); return true; }
                  return false;
                }""")
                if r:
                    log("account row clicked")
            except Exception:
                pass
            try:
                # consent: Continue button
                page.click("button:has-text('Continue'), #submit, button:has-text('Accept')", timeout=2000)
                log("consent clicked")
            except Exception:
                pass
            page.wait_for_timeout(4000)

        page.wait_for_timeout(3000)
        # if a 2FA challenge appears, we stop and ask user — cookie may be enough
        body = ""
        try:
            body = page.inner_text("body")[:600]
        except Exception:
            pass
        if "verify" in body.lower() or "challenge" in body.lower() or "phone" in body.lower():
            log("!! possible 2FA challenge — cookies may be incomplete")

        # dump cookies
        cookies = ctx.cookies()
        keep = [c for c in cookies if "workatastartup" in c.get("domain", "") or "google" in c.get("domain", "")]
        payload = {"cookies": cookies, "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "note": "yc capture via wf SSO profile copy"}
        tmp = OUT + ".tmp"
        json.dump(payload, open(tmp, "w"))
        os.replace(tmp, OUT)
        log(f"saved {len(cookies)} cookies ({len(keep)} workatastartup/google)")
        from collections import Counter
        log(str(Counter(c.get("domain", "?") for c in keep).most_common(8)))
        ctx.close()


if __name__ == "__main__":
    main()
