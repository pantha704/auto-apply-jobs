#!/usr/bin/env python3
"""Bootstrap v2: clear the bad picture upload, then walk the wizard.
Only uploads resume where the UI says resume. Screenshots every step."""
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
                  file: el.type==='file' ? (el.files?.[0]?.name||'') : null,
                  opts: el.tagName==='SELECT' ? [...el.options].slice(0,10).map(o=>o.text.trim().slice(0,30)) : null});
      }
      return out;
    }""")
    btns = page.evaluate("""() => [...document.querySelectorAll('button, a.btn, input[type=submit]')].filter(e => e.offsetParent !== null).map(e => (e.innerText||e.value||'').trim().slice(0,40)).filter(t => t && t.length < 45)""")
    errs = page.evaluate("""() => [...document.querySelectorAll('.error, .text-danger, [class*=error]')].filter(e => e.offsetParent !== null).map(e => e.innerText.trim().slice(0,120)).filter(Boolean)""")
    return fields, btns, errs

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

        for step in range(10):
            cur = page.url
            fields, btns, errs = dump(page)
            log(f"=== STEP {step}: {cur}")
            log("fields: " + json.dumps([{k: (f[k] or '') for k in ('id','ph','tag','type','val','file')} for f in fields], ensure_ascii=False))
            log("buttons: " + json.dumps(btns))
            if errs:
                log("ERRORS: " + json.dumps(errs))

            # 1. clear any file inputs holding a PDF in a picture context
            try:
                pic_cleared = page.evaluate("""() => {
                  let n = 0;
                  for (const el of document.querySelectorAll('input[type=file]')) {
                    if (el.files && el.files.length && el.files[0].name.toLowerCase().includes('.pdf')) {
                      // find nearby X/remove button
                      const wrap = el.closest('.file, .upload, div') ;
                      const x = wrap ? wrap.querySelector('[class*=remove], [class*=delete], .fa-times, [class*=close]') : null;
                      if (x) { x.click(); n++; }
                    }
                  }
                  return n;
                }""")
                if pic_cleared:
                    log(f"removed {pic_cleared} bad file(s)")
                    page.wait_for_timeout(1500)
            except Exception as e:
                log(f"pic clear err: {e}")

            # 2. fill known fields
            body_low = page.inner_text("body").lower()
            for i, f in enumerate(fields):
                key = (f["id"] + " " + f["ph"]).lower()
                if f["val"].strip() or f["tag"] == "SELECT" and f["val"]:
                    continue
                if f["type"] == "file":
                    if "resume" in body_low or "cv" in key:
                        try:
                            page.locator("input[type=file]").nth(0).set_input_files(RESUME, timeout=8000)
                            log("resume uploaded (resume context)")
                        except Exception as e:
                            log(f"resume up err: {e}")
                    continue
                for datakey, val in {
                    "college": "Sister Nivedita University", "university": "Sister Nivedita University",
                    "degree": "B.Tech", "branch": "", "city": "Kolkata",
                    "graduation": "2027", "year": "2027", "start": "2023",
                }.items():
                    if val and datakey in key:
                        r = set_value(page, i, val)
                        log(f"fill {key!r} <- {val!r}: {r}")

            # 3. advance
            clicked = False
            for lab in ["Confirm and continue", "Save and continue", "Continue", "Save", "Submit", "Next"]:
                try:
                    b = page.locator(f"button:has-text('{lab}'), a:has-text('{lab}'), input[type=submit][value*='{lab}']").first
                    if b.is_visible(timeout=1500):
                        b.click(timeout=8000)
                        log(f"clicked: {lab}")
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                try:
                    sk = page.locator("a:has-text('Skip'), button:has-text('Skip')").first
                    if sk.is_visible(timeout=1500):
                        sk.click(timeout=6000)
                        log("clicked skip")
                        clicked = True
                except Exception:
                    pass
            page.screenshot(path=f"/tmp/is_w2_{step}.png")
            if not clicked:
                log("STUCK — no primary action")
                break
            page.wait_for_timeout(5000)
            if page.url == cur and step > 0 and not errs:
                log("URL unchanged after click — possible validation block, screenshot saved")
            if "/student/dashboard" in page.url:
                log("REACHED DASHBOARD")
                break

        page.screenshot(path="/tmp/is_w2_final.png")
        log("final: " + page.url)

if __name__ == "__main__":
    main()
