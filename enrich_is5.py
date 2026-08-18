#!/usr/bin/env python3
"""Enrich v5: dump profile-select options, then fully interactive fill with dropdown clicks."""
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

        # 1. dump profile select options
        opts = page.evaluate("""() => {
          const el = document.getElementById('experience_profile');
          return el ? [...el.options].map(o => o.text.trim()) : [];
        }""")
        log("profile options: " + json.dumps(opts))

        # 2. fill plain inputs
        for fid, val in [("experience_designation", JOB["role"]),
                         ("experience_organization", JOB["company"])]:
            try:
                page.locator(f"#{fid}").first.fill(val, timeout=6000)
                log(f"{fid} filled")
            except Exception as e:
                log(f"{fid} err: {str(e)[:50]}")

        # 3. profile select — pick best option
        best = None
        for cand in ["Information Technology", "IT", "Software", "Engineering", "Computer", "Operations"]:
            for o in opts:
                if cand.lower() in o.lower():
                    best = o
                    break
            if best:
                break
        log(f"best profile option: {best}")
        if best:
            try:
                # click the select2/chosen container to open, then the option
                page.click("#experience_profile", timeout=6000)
                page.wait_for_timeout(1200)
                for sel in [f"li:has-text('{best}')", f"option:has-text('{best}')", f"text={best}"]:
                    try:
                        s = page.locator(sel).first
                        if s.is_visible(timeout=2000):
                            s.click(timeout=4000)
                            log(f"profile chosen via {sel}")
                            break
                    except Exception:
                        continue
            except Exception as e:
                log(f"profile click err: {str(e)[:50]}")
        page.wait_for_timeout(800)
        page.screenshot(path="/tmp/is_v5_after_profile.png")

        # 4. location type-ahead
        try:
            page.locator("#experience_location").first.fill("Kolkata", timeout=6000)
            page.wait_for_timeout(2000)
            for sel in ["li:has-text('Kolkata, West Bengal')", ".modal.show li:has-text('Kolkata')", "li:has-text('Kolkata')"]:
                try:
                    s = page.locator(sel).first
                    if s.is_visible(timeout=2000):
                        s.click(timeout=4000)
                        log(f"location picked via {sel}")
                        break
                except Exception:
                    continue
        except Exception as e:
            log(f"loc err: {str(e)[:50]}")
        page.screenshot(path="/tmp/is_v5_after_loc.png")

        # 5. dates: click field to open picker, choose Apr 2026
        try:
            page.click("#experience_start_date", timeout=6000)
            page.wait_for_timeout(1500)
            # datepicker UI: look for year/month pickers
            page.screenshot(path="/tmp/is_v5_datepicker.png")
            # try clicking month/year text selects in picker
            for sel in [".datepicker-days th:has-text('2026')", "select.ui-datepicker-year", ".ui-datepicker-year"]:
                try:
                    e = page.locator(sel).first
                    if e.is_visible(timeout=1500):
                        if e.evaluate("el => el.tagName") == "SELECT":
                            e.select_option("2026", timeout=3000)
                            log("year 2026 selected")
                        else:
                            e.click(timeout=3000)
                            log(f"clicked {sel}")
                except Exception:
                    continue
        except Exception as e:
            log(f"date err: {str(e)[:60]}")
        page.screenshot(path="/tmp/is_v5_after_date.png")

        # 6. checkboxes
        for cb_id in ["experience_is_work_from_home", "experience_on_going"]:
            r = page.evaluate(f"""() => {{
              const el = document.getElementById('{cb_id}');
              if (!el) return 'missing';
              if (!el.checked) el.click();
              return el.checked ? 'checked' : 'unchecked';
            }}""")
            log(f"{cb_id}: {r}")
        page.wait_for_timeout(800)

        # 7. description
        page.evaluate(f"""() => {{
          const el = document.querySelector('.modal.show textarea') || document.getElementById('experience_description');
          if (el) {{
            const proto = window.HTMLTextAreaElement.prototype;
            Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, {json.dumps(JOB['desc'])});
            el.dispatchEvent(new Event('input', {{bubbles:true}}));
          }}
        }}""")
        log("desc set")

        page.screenshot(path="/tmp/is_v5_before_save.png")
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
        page.screenshot(path="/tmp/is_v5_final.png")
        txt = page.inner_text("body").lower()
        log("verify bfhr: " + str("bfhr" in txt or "braid" in txt))

if __name__ == "__main__":
    main()
