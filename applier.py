#!/usr/bin/env python3
"""Interactive LinkedIn Easy Apply driver — one browser session, command-file driven.
Commands (JSON list of actions) in /tmp/applier_cmd.json:
  {"kind":"text","label":"...","value":"..."}        fill by label
  {"kind":"select","label":"...","value":"..."}      select option by label
  {"kind":"click","label":"..."}                     click button by text regex
  {"kind":"upload","value":"/path/file.pdf"}         set file input
  {"kind":"dump"}                                    write state to /tmp/applier_out.json
  {"kind":"wait","ms":1500}                          pause
  {"kind":"close"}                                   end session
"""
from playwright.sync_api import sync_playwright
import json, os
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome", re, time, os, sys

PROFILE = "/home/ubuntu/.config/google-chrome/Profile 4"
CMD = "/tmp/applier_cmd.json"
OUT = "/tmp/applier_out.json"
STEALTH_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

def field_info(page):
    return page.evaluate("""() => {
      const out = [];
      for (const el of document.querySelectorAll('input, textarea, select, button')) {
        if (el.offsetParent === null) continue;
        const tag = el.tagName.toLowerCase();
        let label = el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
        if (!label && el.id) {
          const lab = document.querySelector(`label[for="${el.id}"]`);
          if (lab) label = lab.innerText.trim();
        }
        if (!label && el.closest('.artdeco-text-input--container, .fb-dash-form-element')) {
          label = (el.closest('.artdeco-text-input--container, .fb-dash-form-element').innerText || '').replace(el.value||'','').trim().slice(0,80);
        }
        if (tag === 'button') {
          const t = (el.innerText || '').trim().slice(0,50);
          if (t) out.push({kind:'button', text:t, aria:el.getAttribute('aria-label')||''});
          continue;
        }
        out.push({kind: tag, type: el.type||'', label: label.slice(0,90),
                  value: (el.value||'').slice(0,120),
                  required: el.required || el.getAttribute('aria-required')==='true'});
      }
      return out;
    }""")

def dump(page, step, extra=None):
    st = {"step": step, "url": page.url, "fields": field_info(page),
          "body_head": page.inner_text("body")[:2500]}
    if extra: st.update(extra)
    json.dump(st, open(OUT, "w"), indent=1, ensure_ascii=False)
    print(f"[dump:{step}] {page.url}", flush=True)

def wait_cmd():
    while True:
        if os.path.exists(CMD):
            try:
                cmds = json.load(open(CMD))
                os.remove(CMD)
                return cmds
            except Exception:
                time.sleep(0.5)
        time.sleep(0.7)

def main(url):
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE, executable_path=CLOAK, headless=False,
            args=["--no-first-run", "--no-default-browser-check", "--disable-sync",
                  "--disable-blink-features=AutomationControlled", "--window-size=1400,900"])
        ctx.add_init_script(STEALTH_JS)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        for sel in ["button[aria-label='Dismiss']", "button[aria-label='Close']"]:
            try: page.click(sel, timeout=1200)
            except Exception: pass
        try:
            page.click("button:has-text('Easy Apply')", timeout=8000)
            page.wait_for_timeout(2000)
            dump(page, "form_open")
        except Exception as e:
            dump(page, "no_easy_apply", {"error": str(e)[:150]})
            ctx.close(); return

        while True:
            cmds = wait_cmd()
            for c in cmds:
                if c.get("kind") == "close":
                    ctx.close(); return
                try:
                    if c["kind"] == "dump":
                        dump(page, c.get("note", "state"))
                    elif c["kind"] == "wait":
                        time.sleep(c.get("ms", 1500) / 1000)
                    elif c["kind"] == "text":
                        if "selector" in c:
                            page.fill(c["selector"], c["value"])
                        else:
                            page.get_by_label(re.compile(re.escape(c["label"]), re.I)).first.fill(c["value"])
                        print("text:", c.get("note", c.get("label", c.get("selector", ""))), "->", str(c["value"])[:40], flush=True)
                    elif c["kind"] == "select":
                        page.get_by_label(re.compile(re.escape(c["label"]), re.I)).first.select_option(c["value"])
                        print("select:", c["label"], "->", c["value"], flush=True)
                    elif c["kind"] == "click":
                        page.get_by_role("button", name=re.compile(c["label"], re.I)).first.click(timeout=4000)
                        print("click:", c["label"], flush=True)
                        page.wait_for_timeout(1500)
                    elif c["kind"] == "radio":
                        page.get_by_role("radio", name=re.compile(c["label"], re.I)).first.check(timeout=4000)
                        print("radio:", c["label"], flush=True)
                        page.wait_for_timeout(800)
                    elif c["kind"] == "js":
                        res = page.evaluate(c["script"])
                        print("js:", c.get("note", ""), "->", res, flush=True)
                        page.wait_for_timeout(800)
                    elif c["kind"] == "upload":
                        page.set_input_files("input[type=file]", c["value"])
                        print("upload:", c["value"], flush=True)
                        page.wait_for_timeout(2000)
                    elif c["kind"] == "select_resume":
                        res = page.evaluate("""() => {
                          const radios = [...document.querySelectorAll('input[type=radio]')];
                          const checked = radios.find(r => r.checked);
                          if (checked) return 'already-selected';
                          const target = radios.find(r => {
                            let p = r.parentElement;
                            for (let i=0;i<5 && p;i++){ if ((p.innerText||'').includes('Pratham_Jaiswal_Updated_Resume')) return true; p = p.parentElement; }
                            return false;
                          });
                          if (target) { target.click(); return 'selected-existing'; }
                          if (radios.length) { radios[0].click(); return 'selected-first-radio'; }
                          return 'no-radios-need-upload';
                        }""")
                        print("resume:", res, flush=True)
                        page.wait_for_timeout(1200)
                    elif c["kind"] == "upload_btn":
                        with page.expect_file_chooser(timeout=8000) as fc:
                            page.get_by_role("button", name=re.compile(c.get("label", "Upload resume"), re.I)).first.click()
                        fc.value.set_files(c["value"])
                        print("upload_btn:", c["value"], flush=True)
                        page.wait_for_timeout(2500)
                except Exception as e:
                    print("ACTION FAIL:", c, str(e)[:150], flush=True)
            dump(page, "after_cmds")

if __name__ == "__main__":
    main(sys.argv[1])
