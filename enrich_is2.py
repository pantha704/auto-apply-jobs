#!/usr/bin/env python3
"""Enrich v2: modal-scoped saves, wait for modal close between sections."""
import json, os, sys, time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
HERE = "/home/ubuntu/job_hunt_linkedin"
DST = os.path.join(HERE, "profiles", "is_login")
STEALTH = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

OBJECTIVE = ("Automation engineer and full-stack developer building business workflows, API "
             "integrations, and asynchronous services. Hands-on with TypeScript, Python, Node.js, "
             "n8n, REST/webhooks, Docker, and PostgreSQL; four merged open-source contributions.")
SKILLS = ["Python", "TypeScript", "JavaScript", "React", "Node.js", "Next.js", "PostgreSQL",
          "Docker", "REST API", "WebSockets", "n8n", "Microsoft 365", "Git", "Linux"]
JOB = {"role": "Full-Stack Developer - IT Systems & Operations",
       "company": "Braid-Forbes Health Research (BFHR)",
       "start": "Apr 2026",
       "desc": ("Configure monday.com and Outlook ticket automations; administer Microsoft 365, "
                "Teams, Exchange, and endpoint security (ThreatDown Nebula); troubleshoot VPN/MFA/RDP "
                "for distributed staff; build vulnerability reports and document repeatable fixes.")}

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def save_modal(page):
    """Click save inside any open modal; wait for it to close."""
    for sel in [".modal.show button:has-text('Save')", ".modal.show button:has-text('Add')",
                ".modal.show button:has-text('Done')", ".modal.show input[type=submit]",
                "button:has-text('Save')"]:
        try:
            b = page.locator(sel).first
            if b.is_visible(timeout=1500):
                b.click(timeout=6000)
                log(f"saved via {sel}")
                # wait for modal to close
                for _ in range(6):
                    page.wait_for_timeout(1000)
                    if not page.locator(".modal.show").count():
                        log("modal closed")
                        return True
                log("modal still open after save")
                return True
        except Exception:
            continue
    return False

def set_val(page, idx, value):
    return page.evaluate(f"""() => {{
      const els = [...document.querySelectorAll('.modal.show input, .modal.show select, .modal.show textarea, input, select, textarea')].filter(e => e.offsetParent !== null);
      const el = els[{idx}];
      if (!el) return 'missing';
      if (el.tagName === 'SELECT') {{
        const v = {json.dumps(value.lower())};
        const opt = [...el.options].find(o => (o.text||'').trim().toLowerCase().includes(v));
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
        page.set_default_timeout(20000)

        page.goto("https://internshala.com/student/resume", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(4000)

        # 0. close any lingering modal (from previous partial run)
        try:
            if page.locator(".modal.show").count():
                page.keyboard.press("Escape")
                page.wait_for_timeout(1500)
                log("escaped lingering modal")
        except Exception:
            pass

        # 1. career objective
        try:
            txt = page.inner_text("body").lower()
            if "automation engineer" not in txt:
                page.click("text=Add your career objective", timeout=6000)
                page.wait_for_timeout(2000)
                ta = page.locator("textarea#career_objective_description").first
                if ta.count() > 0:
                    ta.fill(OBJECTIVE, timeout=6000)
                    log("objective filled")
                save_modal(page)
            else:
                log("objective already present")
        except Exception as e:
            log(f"objective err: {e}")
        page.screenshot(path="/tmp/is_v2_obj.png")

        # 2. skills
        try:
            txt = page.inner_text("body").lower()
            if "python" not in txt:
                page.click("text=Add skill", timeout=6000)
                page.wait_for_timeout(2500)
                for sk in SKILLS:
                    try:
                        inp = page.locator(".modal.show input[type=text], .modal.show input[type=search]").first
                        inp.fill(sk, timeout=4000)
                        page.wait_for_timeout(1500)
                        hit = False
                        for sel in [f".modal.show li:has-text('{sk}')", f".modal.show div:has-text('{sk}')", f"text={sk}"]:
                            try:
                                el = page.locator(sel).first
                                if el.is_visible(timeout=2000):
                                    el.click(timeout=3000)
                                    log(f"skill added: {sk}")
                                    hit = True
                                    break
                            except Exception:
                                continue
                        if not hit:
                            log(f"skill {sk}: no suggestion hit, pressing Enter")
                            inp.press("Enter", timeout=3000)
                        page.wait_for_timeout(700)
                    except Exception as e:
                        log(f"skill {sk} err: {str(e)[:60]}")
                save_modal(page)
            else:
                log("skills already present")
        except Exception as e:
            log(f"skills err: {e}")
        page.screenshot(path="/tmp/is_v2_skills.png")

        # 3. work experience
        try:
            txt = page.inner_text("body").lower()
            if "bfhr" not in txt and "braid" not in txt:
                page.click("text=Add job", timeout=6000)
                page.wait_for_timeout(2500)
                fields = page.evaluate("""() => {
                  const out = [];
                  for (const el of document.querySelectorAll('.modal.show input, .modal.show select, .modal.show textarea')) {
                    if (el.offsetParent === null) continue;
                    out.push({tag: el.tagName, id: el.id||'', ph: (el.getAttribute('placeholder')||'').slice(0,45), val: (el.value||'').slice(0,35)});
                  }
                  return out;
                }""")
                log("job fields: " + json.dumps(fields, ensure_ascii=False))
                for i, f in enumerate(fields):
                    key = (f["id"] + " " + f["ph"]).lower()
                    if f["val"].strip():
                        continue
                    if "role" in key or "designation" in key or "title" in key:
                        log("role: " + set_val(page, i, JOB["role"]))
                    elif "company" in key or "organisation" in key or "organization" in key:
                        log("company: " + set_val(page, i, JOB["company"]))
                    elif "start" in key or "joining" in key:
                        log("start: " + set_val(page, i, JOB["start"]))
                    elif f["tag"] == "TEXTAREA" or "describe" in key or "responsib" in key:
                        log("desc: " + set_val(page, i, JOB["desc"]))
                try:
                    cb = page.locator(".modal.show input[type=checkbox]").first
                    if cb.count() > 0 and not cb.is_checked():
                        cb.check(timeout=3000)
                        log("checked currently-working")
                except Exception:
                    pass
                save_modal(page)
            else:
                log("job already present")
        except Exception as e:
            log(f"job err: {e}")
        page.screenshot(path="/tmp/is_v2_job.png")

        # final verification
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        txt = page.inner_text("body").lower()
        log("verify: " + json.dumps({
            "objective": "automation engineer" in txt,
            "python": "python" in txt,
            "bfhr": "bfhr" in txt or "braid" in txt,
        }))
        page.screenshot(path="/tmp/is_v2_final.png")

if __name__ == "__main__":
    main()
