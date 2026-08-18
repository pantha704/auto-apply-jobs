#!/usr/bin/env python3
"""Enrich v6: autocomplete-aware. Profile + location via suggestion clicks, date via picker."""
import json, os, sys, time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
HERE = "/home/ubuntu/job_hunt_linkedin"
DST = os.path.join(HERE, "profiles", "is_login")
STEALTH = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

JOB = {"role": "Full-Stack Developer - IT Systems & Operations",
       "company": "Braid-Forbes Health Research (BFHR)",
       "desc": ("Configure monday.com and Outlook ticket automations; administer Microsoft 365, "
                "Teams, Exchange, and endpoint security; troubleshoot VPN/MFA/RDP for distributed staff.")}

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=DST, executable_path=CLOAK, headless=True,
            args=["--no-first-run", "--no-default-browser-check",
                  "--disable-blink-features=AutomationControlled", "--window-size=1400,900"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.add_init_script(STEALTH)
        page.set_default_timeout(20000)

        page.goto("https://internshala.com/student/resume", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(4000)
        txt = page.inner_text("body").lower()
        if "bfhr" in txt or "braid" in txt:
            log("job already saved")
            return

        page.click("text=Add job", timeout=6000)
        page.wait_for_timeout(2500)

        # plain inputs
        for fid, val in [("experience_designation", JOB["role"]),
                         ("experience_organization", JOB["company"])]:
            try:
                page.locator(f"#{fid}").first.fill(val, timeout=6000)
                log(f"{fid} filled")
            except Exception as e:
                log(f"{fid} err: {str(e)[:50]}")

        # profile autocomplete
        try:
            page.locator("#experience_profile").first.fill("Operations", timeout=6000)
            page.wait_for_timeout(2000)
            page.screenshot(path="/tmp/is_v6_profile_dd.png")
            hit = False
            for sel in ["li:has-text('Logistics and Operations')", "li:has-text('Operations')",
                        ".ui-menu-item:has-text('Operations')", "div:has-text('Logistics and Operations')"]:
                try:
                    s = page.locator(sel).first
                    if s.is_visible(timeout=2000):
                        s.click(timeout=4000)
                        log(f"profile picked via {sel}")
                        hit = True
                        break
                except Exception:
                    continue
            if not hit:
                log("no profile suggestion hit — pressing Enter")
                page.keyboard.press("Enter", timeout=3000)
        except Exception as e:
            log(f"profile err: {str(e)[:50]}")
        page.wait_for_timeout(800)

        # location autocomplete
        try:
            page.locator("#experience_location").first.fill("Kolkata", timeout=6000)
            page.wait_for_timeout(2000)
            page.screenshot(path="/tmp/is_v6_loc_dd.png")
            hit = False
            for sel in ["li:has-text('Kolkata, West Bengal')", "li:has-text('Kolkata')",
                        ".ui-menu-item:has-text('Kolkata')"]:
                try:
                    s = page.locator(sel).first
                    if s.is_visible(timeout=2000):
                        s.click(timeout=4000)
                        log(f"location picked via {sel}")
                        hit = True
                        break
                except Exception:
                    continue
            if not hit:
                page.keyboard.press("Enter", timeout=3000)
        except Exception as e:
            log(f"loc err: {str(e)[:50]}")
        page.wait_for_timeout(800)

        # start date: click + interact with picker
        try:
            page.click("#experience_start_date", timeout=6000)
            page.wait_for_timeout(1500)
            page.screenshot(path="/tmp/is_v6_datepicker.png")
            # try month-year selects in picker (bootstrap datepicker style)
            for sel in ["select.ui-datepicker-month", ".datepicker-months select", "th.datepicker-switch"]:
                try:
                    e = page.locator(sel).first
                    if e.is_visible(timeout=1500):
                        tag = e.evaluate("el => el.tagName")
                        log(f"picker element {sel} tag={tag}")
                        if tag == "SELECT":
                            e.select_option(index=3, timeout=3000)  # Apr
                            log("month select -> Apr")
                except Exception:
                    continue
            # select year 2026 if a year select exists
            try:
                ys = page.locator("select.ui-datepicker-year").first
                if ys.is_visible(timeout=1500):
                    ys.select_option("2026", timeout=3000)
                    log("year -> 2026")
            except Exception:
                pass
            # click day 1
            try:
                d = page.locator(".ui-datepicker-calendar a:has-text('1')").first
                if d.is_visible(timeout=1500):
                    d.click(timeout=3000)
                    log("day 1 clicked")
            except Exception:
                pass
            page.screenshot(path="/tmp/is_v6_after_date.png")
        except Exception as e:
            log(f"date err: {str(e)[:60]}")

        # checkboxes
        for cb_id in ["experience_is_work_from_home", "experience_on_going"]:
            r = page.evaluate(f"""() => {{
              const el = document.getElementById('{cb_id}');
              if (!el) return 'missing';
              if (!el.checked) el.click();
              return el.checked ? 'checked' : 'unchecked';
            }}""")
            log(f"{cb_id}: {r}")
        page.wait_for_timeout(800)

        # description
        page.evaluate(f"""() => {{
          const el = document.querySelector('.modal.show textarea') || document.getElementById('experience_description');
          if (el) {{
            const proto = window.HTMLTextAreaElement.prototype;
            Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, {json.dumps(JOB['desc'])});
            el.dispatchEvent(new Event('input', {{bubbles:true}}));
          }}
        }}""")

        page.screenshot(path="/tmp/is_v6_before_save.png")
        try:
            page.click(".modal.show button:has-text('Save')", timeout=6000)
            log("save clicked")
        except Exception as e:
            log(f"save err: {str(e)[:60]}")
        page.wait_for_timeout(4500)
        for _ in range(6):
            if not page.locator(".modal.show").count():
                log("modal closed")
                break
            page.wait_for_timeout(1500)
        page.screenshot(path="/tmp/is_v6_final.png")
        txt = page.inner_text("body").lower()
        log("verify bfhr: " + str("bfhr" in txt or "braid" in txt))

if __name__ == "__main__":
    main()
