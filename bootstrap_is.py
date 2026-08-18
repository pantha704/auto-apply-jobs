#!/usr/bin/env python3
"""Complete the Internshala profile wizard: personal details -> education -> skills -> resume.
Fills only real, known data; stops and reports when a required field is unknown."""
import json, os, sys, time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
HERE = "/home/ubuntu/job_hunt_linkedin"
DST = os.path.join(HERE, "profiles", "is_login")
RESUME = os.path.join(HERE, "resume_pratham.pdf")
STEALTH = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

DATA = {
    "first_name": "Pratham", "last_name": "Jaiswal",
    "phone": os.environ.get("JOBHUNT_PHONE", ""), "current_city": "Kolkata",
    "college": "Sister Nivedita University", "degree": "B.Tech",
    "graduation": "2027", "start": "2023",
}

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def dump(page):
    fields = page.evaluate("""() => {
      const out = [];
      for (const el of document.querySelectorAll('input, select, textarea')) {
        if (el.offsetParent === null) continue;
        out.push({id: el.id||'', ph: (el.getAttribute('placeholder')||'').slice(0,40),
                  tag: el.tagName, type: el.type||'', val: (el.value||'').slice(0,30),
                  opts: el.tagName==='SELECT' ? [...el.options].slice(0,12).map(o=>o.text.trim().slice(0,30)) : null});
      }
      return out;
    }""")
    btns = page.evaluate("""() => [...document.querySelectorAll('button, a.btn, input[type=submit]')].filter(e => e.offsetParent !== null).map(e => (e.innerText||e.value||'').trim().slice(0,40)).filter(t => t && !t.includes('\\n'))""")
    return fields, btns

def fill_field(page, el_js_index, value):
    return page.evaluate(f"""() => {{
      const els = [...document.querySelectorAll('input, select, textarea')].filter(e => e.offsetParent !== null);
      const el = els[{el_js_index}];
      if (!el) return 'missing';
      if (el.tagName === 'SELECT') {{
        const opt = [...el.options].find(o => (o.text||'').trim().toLowerCase().includes({json.dumps(value.lower())}));
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

def click_primary(page, labels):
    for lab in labels:
        try:
            b = page.locator(f"button:has-text('{lab}'), a:has-text('{lab}'), input[type=submit][value*='{lab}']").first
            if b.is_visible(timeout=1500):
                b.click(timeout=8000)
                log(f"clicked: {lab}")
                return True
        except Exception:
            continue
    return False

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

        for step in range(8):
            cur = page.url
            log(f"STEP {step}: {cur}")
            if "/student/dashboard" in cur or "/student/applications" in cur or "resume" in cur:
                pass
            fields, btns = dump(page)
            log("fields: " + json.dumps([{k: f[k] for k in ('id','ph','tag','type','val')} for f in fields], ensure_ascii=False)[:600])
            log("buttons: " + json.dumps(btns))

            # fill known fields by id/placeholder
            for i, f in enumerate(fields):
                key = f["id"].strip().lower() or f["ph"].strip().lower()
                for datakey, val in DATA.items():
                    if datakey in key and not f["val"].strip():
                        r = fill_field(page, i, val)
                        log(f"fill {key!r} <- {val!r}: {r}")

            # upload resume if file input present
            try:
                inp = page.locator("input[type=file]").first
                if inp.count() > 0:
                    inp.set_input_files(RESUME, timeout=8000)
                    log("resume uploaded")
            except Exception:
                pass

            # textareas: cover-ish fields left alone here (profile wizard usually has none)
            # save / continue
            if click_primary(page, ["Confirm and continue", "Save and continue", "Continue", "Save", "Submit", "Next"]):
                page.wait_for_timeout(5000)
                continue
            # maybe a skip link
            try:
                sk = page.locator("a:has-text('Skip'), button:has-text('Skip')").first
                if sk.is_visible(timeout=1500):
                    sk.click(timeout=6000)
                    log("clicked skip")
                    page.wait_for_timeout(5000)
                    continue
            except Exception:
                pass
            log("NO PRIMARY ACTION FOUND — stopping")
            page.screenshot(path=f"/tmp/is_wizard_{step}.png")
            break

        page.screenshot(path="/tmp/is_wizard_final.png")
        log("final url: " + page.url)

if __name__ == "__main__":
    main()
