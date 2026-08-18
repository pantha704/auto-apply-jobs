#!/usr/bin/env python3
"""Enrich v4: JS-setter fills (date picker compatible) + rich-editor description."""
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
                "Teams, Exchange, and endpoint security; troubleshoot VPN/MFA/RDP for distributed "
                "staff; build vulnerability reports.")}

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def js_fill(page, fid, value):
    return page.evaluate(f"""() => {{
      const el = document.getElementById({json.dumps(fid)});
      if (!el) return 'missing';
      if (el.tagName === 'SELECT') {{
        const v = {json.dumps(value.lower())};
        const opt = [...el.options].find(o => (o.text||'').trim().toLowerCase().includes(v));
        if (!opt) return 'no-opt';
        el.value = opt.value;
        el.dispatchEvent(new Event('change', {{bubbles:true}}));
        return 'selected';
      }}
      const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, {json.dumps(value)});
      el.dispatchEvent(new Event('input', {{bubbles:true}}));
      el.dispatchEvent(new Event('change', {{bubbles:true}}));
      return 'filled';
    }}""")

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

        for fid, val in [("experience_designation", JOB["role"]),
                         ("experience_profile", JOB["profile"]),
                         ("experience_organization", JOB["company"]),
                         ("experience_start_date", "Apr 2026")]:
            log(f"{fid}: {js_fill(page, fid, val)}")

        # location via type-ahead + suggestion
        try:
            page.locator("#experience_location").first.fill("Kolkata", timeout=6000)
            page.wait_for_timeout(1800)
            for sel in [".modal.show li:has-text('Kolkata')", "li:has-text('Kolkata, West Bengal')", "li:has-text('Kolkata')"]:
                try:
                    s = page.locator(sel).first
                    if s.is_visible(timeout=2000):
                        s.click(timeout=4000)
                        log(f"location picked via {sel}")
                        break
                except Exception:
                    continue
        except Exception as e:
            log(f"loc err: {str(e)[:60]}")

        # checkboxes via JS click
        for cb_id in ["experience_is_work_from_home", "experience_on_going"]:
            r = page.evaluate(f"""() => {{
              const el = document.getElementById('{cb_id}');
              if (!el) return 'missing';
              if (!el.checked) el.click();
              return el.checked ? 'checked' : 'unchecked';
            }}""")
            log(f"{cb_id}: {r}")
        page.wait_for_timeout(1000)

        # description: rich editor or textarea
        r = page.evaluate(f"""() => {{
          let el = document.querySelector('.modal.show textarea, .modal.show [contenteditable=true], #experience_description');
          if (!el) return 'no-desc-el';
          if (el.isContentEditable) {{ el.innerHTML = {json.dumps('<p>' + JOB['desc'] + '</p>')}; return 'ce-set'; }}
          const proto = window.HTMLTextAreaElement.prototype;
          Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, {json.dumps(JOB['desc'])});
          el.dispatchEvent(new Event('input', {{bubbles:true}}));
          return 'ta-set';
        }}""")
        log(f"desc: {r}")

        page.screenshot(path="/tmp/is_v4_before_save.png")
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
        page.screenshot(path="/tmp/is_v4_final.png")
        txt = page.inner_text("body").lower()
        log("verify bfhr: " + str("bfhr" in txt or "braid" in txt))

if __name__ == "__main__":
    main()
