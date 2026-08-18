#!/usr/bin/env python3
"""Enrich v3: finish the work-experience modal with all required fields."""
import json, os, sys, time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
HERE = "/home/ubuntu/job_hunt_linkedin"
DST = os.path.join(HERE, "profiles", "is_login")
STEALTH = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

JOB = {"role": "Full-Stack Developer - IT Systems & Operations",
       "profile": "Operations",
       "company": "Braid-Forbes Health Research (BFHR)",
       "location": "Kolkata",
       "desc": ("Configure monday.com and Outlook ticket automations; administer Microsoft 365, "
                "Teams, Exchange, and endpoint security (ThreatDown Nebula); troubleshoot VPN/MFA/RDP "
                "for distributed staff; build vulnerability reports and document repeatable fixes.")}

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
            log("job already saved — nothing to do")
            page.screenshot(path="/tmp/is_v3_final.png")
            return

        page.click("text=Add job", timeout=6000)
        page.wait_for_timeout(2500)

        # fill by id
        fills = [
            ("experience_designation", JOB["role"]),
            ("experience_profile", JOB["profile"]),
            ("experience_organization", JOB["company"]),
        ]
        for fid, val in fills:
            try:
                el = page.locator(f"#{fid}").first
                el.fill(val, timeout=6000)
                log(f"{fid} <- {val}")
            except Exception as e:
                log(f"{fid} err: {str(e)[:60]}")

        # location type-ahead
        try:
            el = page.locator("#experience_location").first
            el.fill("Kolkata", timeout=6000)
            page.wait_for_timeout(1800)
            for sel in [".modal.show li:has-text('Kolkata')", ".modal.show div.dropdown-menu li:has-text('Kolkata')", "text=Kolkata, West Bengal"]:
                try:
                    s = page.locator(sel).first
                    if s.is_visible(timeout=2000):
                        s.click(timeout=4000)
                        log(f"location selected via {sel}")
                        break
                except Exception:
                    continue
        except Exception as e:
            log(f"location err: {str(e)[:60]}")

        # start date
        try:
            page.locator("#experience_start_date").first.fill("Apr 2026", timeout=6000)
            log("start date set")
        except Exception as e:
            log(f"start err: {str(e)[:60]}")

        # checkboxes: wfh + currently working (label-click for robustness)
        for cb_id in ["experience_is_work_from_home", "experience_on_going"]:
            try:
                checked = page.evaluate(f"""() => {{
                  const el = document.getElementById('{cb_id}');
                  return el ? el.checked : null;
                }}""")
                if not checked:
                    page.evaluate(f"""() => {{
                      const el = document.getElementById('{cb_id}');
                      if (el) {{ el.click(); }}
                    }}""")
                    log(f"{cb_id} clicked")
                else:
                    log(f"{cb_id} already checked")
            except Exception as e:
                log(f"{cb_id} err: {str(e)[:60]}")
        page.wait_for_timeout(1200)

        # description textarea
        try:
            ta = page.locator(".modal.show textarea").first
            if ta.count() > 0:
                ta.fill(JOB["desc"], timeout=6000)
                log("description filled")
        except Exception as e:
            log(f"desc err: {str(e)[:60]}")

        page.screenshot(path="/tmp/is_v3_before_save.png")
        try:
            page.click(".modal.show button:has-text('Save')", timeout=6000)
            log("save clicked")
        except Exception as e:
            log(f"save err: {str(e)[:60]}")
        page.wait_for_timeout(4000)
        for _ in range(5):
            if not page.locator(".modal.show").count():
                log("modal closed")
                break
            page.wait_for_timeout(1500)

        page.screenshot(path="/tmp/is_v3_final.png")
        txt = page.inner_text("body").lower()
        log("verify bfhr: " + str("bfhr" in txt or "braid" in txt))

if __name__ == "__main__":
    main()
