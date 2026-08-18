#!/usr/bin/env python3
"""Bootstrap v3: select Type=College student, then walk remaining wizard steps."""
import json, os, sys, time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
HERE = "/home/ubuntu/job_hunt_linkedin"
DST = os.path.join(HERE, "profiles", "is_login")
RESUME = os.path.join(HERE, "resume_pratham.pdf")
STEALTH = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def dump(page):
    fields = page.evaluate("""() => {
      const out = [];
      for (const el of document.querySelectorAll('input, select, textarea')) {
        if (el.offsetParent === null) continue;
        out.push({id: el.id||'', ph: (el.getAttribute('placeholder')||'').slice(0,45),
                  tag: el.tagName, type: el.type||'', val: (el.value||'').slice(0,35),
                  opts: el.tagName==='SELECT' ? [...el.options].slice(0,12).map(o=>o.text.trim().slice(0,30)) : null});
      }
      return out;
    }""")
    pills = page.evaluate("""() => {
      const out = [];
      for (const el of document.querySelectorAll('label, div, span, button')) {
        const t = (el.innerText||'').trim();
        if (t.length && t.length < 30 && el.offsetParent !== null &&
            ['College student','Fresher','Working professional','School student','Woman returning to work'].includes(t) && el.children.length <= 2) {
          out.push({tag: el.tagName, text: t, cls: (el.className||'').slice(0,60)});
        }
      }
      return out.slice(0,10);
    }""")
    btns = page.evaluate("""() => [...document.querySelectorAll('button, a.btn, input[type=submit]')].filter(e => e.offsetParent !== null).map(e => (e.innerText||e.value||'').trim().slice(0,40)).filter(t => t && t.length < 45)""")
    errs = page.evaluate("""() => [...document.querySelectorAll('[class*=error], .text-danger')].filter(e => e.offsetParent !== null).map(e => e.innerText.trim().slice(0,100)).filter(Boolean)""")
    return fields, pills, btns, errs

def set_value(page, idx, value):
    return page.evaluate(f"""() => {{
      const els = [...document.querySelectorAll('input, select, textarea')].filter(e => e.offsetParent !== null);
      const el = els[{idx}];
      if (!el) return 'missing';
      if (el.tagName === 'SELECT') {{
        const v = {json.dumps(value.lower())};
        const opt = [...el.options].find(o => (o.text||'').trim().toLowerCase().includes(v) || (o.value||'').toLowerCase().includes(v));
        if (!opt) return 'no-opt:' + [...el.options].slice(0,8).map(o=>o.text.trim()).join('|');
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
        page.set_default_timeout(30000)

        page.goto("https://internshala.com/student/personal_details", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(4000)

        # STEP A: select Type = College student
        try:
            page.click("text=College student", timeout=8000)
            log("selected: College student")
            page.wait_for_timeout(1500)
        except Exception as e:
            log(f"type click err: {e}")

        for step in range(10):
            fields, pills, btns, errs = dump(page)
            log(f"=== STEP {step}: {page.url}")
            log("fields: " + json.dumps([{k: (f[k] or '') for k in ('id','ph','tag','type','val')} for f in fields], ensure_ascii=False))
            log("buttons: " + json.dumps(btns))
            if errs:
                log("ERRORS: " + json.dumps(errs))
            if step == 0:
                # confirm personal details
                try:
                    page.click("button:has-text('Confirm and continue')", timeout=8000)
                    log("confirmed personal details")
                    page.wait_for_timeout(5000)
                    continue
                except Exception as e:
                    log(f"confirm err: {e}")

            # education-ish fills
            for i, f in enumerate(fields):
                key = (f["id"] + " " + f["ph"]).lower()
                if f["val"].strip():
                    continue
                for datakey, val in {
                    "college": "Sister Nivedita University", "university": "Sister Nivedita University",
                    "institute": "Sister Nivedita University", "degree": "B.Tech",
                    "graduation": "2027", "passing": "2027", "start": "2023",
                }.items():
                    if val and datakey in key:
                        r = set_value(page, i, val)
                        log(f"fill {key!r} <- {val!r}: {r}")

            clicked = False
            for lab in ["Save and continue", "Continue", "Save", "Submit", "Next", "Confirm and continue"]:
                try:
                    b = page.locator(f"button:has-text('{lab}'), a:has-text('{lab}'), input[type=submit][value*='{lab}']").first
                    if b.is_visible(timeout=1500):
                        b.click(timeout=8000)
                        log(f"clicked: {lab}")
                        clicked = True
                        break
                except Exception:
                    continue
            page.screenshot(path=f"/tmp/is_w3_{step}.png")
            if not clicked:
                log("STUCK")
                break
            page.wait_for_timeout(5000)
            if "/student/dashboard" in page.url:
                log("REACHED DASHBOARD")
                break

        page.screenshot(path="/tmp/is_w3_final.png")
        log("final: " + page.url)

if __name__ == "__main__":
    main()
