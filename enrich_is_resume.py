#!/usr/bin/env python3
"""Enrich the Internshala profile resume: career objective, skills, work experience."""
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
          "Docker", "REST APIs", "WebSockets", "n8n", "Microsoft 365", "Git", "Linux"]
JOB = {"role": "Full-Stack Developer - IT Systems & Operations",
       "company": "Braid-Forbes Health Research (BFHR)",
       "start": "Apr 2026", "desc": ("Configure monday.com and Outlook ticket automations; administer "
       "Microsoft 365, Teams, Exchange, and endpoint security (ThreatDown Nebula); troubleshoot "
       "VPN/MFA/RDP for distributed staff; build vulnerability reports and document repeatable fixes.")}

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def visible_fields(page):
    return page.evaluate("""() => {
      const out = [];
      for (const el of document.querySelectorAll('input, select, textarea')) {
        if (el.offsetParent === null) continue;
        out.push({tag: el.tagName, type: el.type||'', id: el.id||'', ph: (el.getAttribute('placeholder')||'').slice(0,50),
                  val: (el.value||'').slice(0,40), opts: el.tagName==='SELECT' ? [...el.options].slice(0,8).map(o=>o.text.trim().slice(0,25)) : null});
      }
      return out;
    }""")

def set_val(page, idx, value):
    return page.evaluate(f"""() => {{
      const els = [...document.querySelectorAll('input, select, textarea')].filter(e => e.offsetParent !== null);
      const el = els[{idx}];
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

def save_modal(page):
    for lab in ["Save", "Add", "Done", "Submit", "Save details"]:
        try:
            b = page.locator(f"button:has-text('{lab}'), a:has-text('{lab}'), input[type=submit][value*='{lab}']").first
            if b.is_visible(timeout=1500):
                b.click(timeout=6000)
                log(f"modal saved via {lab}")
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
        page.set_default_timeout(20000)

        page.goto("https://internshala.com/student/resume", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(4000)

        # 1. career objective
        try:
            page.click("text=Add your career objective", timeout=6000)
            page.wait_for_timeout(2000)
            fields = visible_fields(page)
            log("objective fields: " + json.dumps(fields, ensure_ascii=False))
            for i, f in enumerate(fields):
                if f["tag"] == "TEXTAREA" and not f["val"].strip():
                    log("objective set: " + set_val(page, i, OBJECTIVE))
            save_modal(page)
            page.wait_for_timeout(2500)
        except Exception as e:
            log(f"objective err: {e}")
        page.screenshot(path="/tmp/is_obj.png")

        # 2. skills
        try:
            page.click("text=Add skill", timeout=6000)
            page.wait_for_timeout(2500)
            for sk in SKILLS:
                try:
                    inp = page.locator("input[type=text], input[type=search]").first
                    inp.fill(sk, timeout=4000)
                    page.wait_for_timeout(1200)
                    # click suggestion dropdown item
                    for sel in [f"text={sk}", "li:has-text('" + sk + "')", ".dropdown-item:has-text('" + sk + "')"]:
                        try:
                            page.click(sel, timeout=3000)
                            log(f"skill added: {sk}")
                            break
                        except Exception:
                            continue
                    page.wait_for_timeout(800)
                except Exception as e:
                    log(f"skill {sk} err: {e}")
            save_modal(page)
            page.wait_for_timeout(2500)
        except Exception as e:
            log(f"skills err: {e}")
        page.screenshot(path="/tmp/is_skills.png")

        # 3. work experience
        try:
            page.click("text=Add job", timeout=6000)
            page.wait_for_timeout(2500)
            fields = visible_fields(page)
            log("job fields: " + json.dumps(fields, ensure_ascii=False))
            for i, f in enumerate(fields):
                key = (f["id"] + " " + f["ph"]).lower()
                if f["val"].strip():
                    continue
                if "role" in key or "designation" in key or "title" in key or ("profile" in key and f["tag"] != "SELECT"):
                    log("role: " + set_val(page, i, JOB["role"]))
                elif "company" in key or "organisation" in key or "organization" in key:
                    log("company: " + set_val(page, i, JOB["company"]))
                elif "start" in key or "joining" in key:
                    log("start: " + set_val(page, i, JOB["start"]))
                elif "describe" in key or "responsib" in key or f["tag"] == "TEXTAREA":
                    log("desc: " + set_val(page, i, JOB["desc"]))
            # currently working checkbox if present
            try:
                cb = page.locator("input[type=checkbox]").first
                if cb.count() > 0 and not cb.is_checked():
                    cb.check(timeout=3000)
                    log("checked currently-working")
            except Exception:
                pass
            save_modal(page)
            page.wait_for_timeout(2500)
        except Exception as e:
            log(f"job err: {e}")
        page.screenshot(path="/tmp/is_job.png")

        # final verification
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        txt = page.inner_text("body")
        checks = {
            "objective": "automation engineer" in txt.lower(),
            "skills_python": "python" in txt.lower(),
            "bfhr": "bfhr" in txt.lower() or "braid" in txt.lower(),
        }
        log("verification: " + json.dumps(checks))
        page.screenshot(path="/tmp/is_final_enrich.png")

if __name__ == "__main__":
    main()
