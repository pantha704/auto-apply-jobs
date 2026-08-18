#!/usr/bin/env python3
"""Headed Wellfound profile-builder session. Command-file driven like applier."""
import json, os, re, time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
STATE = "/home/ubuntu/Documents/job_hunt_linkedin/portal_wellfound.json"
HERE = os.path.dirname(os.path.abspath(__file__))
CMD = os.path.join(HERE, "wf_cmd.json")
OUT = os.path.join(HERE, "wf_out.json")

def dump(page, step, extra=None):
    st = {"step": step, "url": page.url,
          "body": page.inner_text("body")[:1500],
          "fields": page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('input, textarea, select, button').forEach(el => {
              let lab = el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
              if (!lab && el.id) { const l = document.querySelector(`label[for="${el.id}"]`); if (l) lab = l.innerText; }
              if (el.tagName === 'BUTTON') { const t=(el.innerText||'').trim(); if (t) out.push('BTN:' + t.slice(0,35)); return; }
              out.push((el.tagName + '.' + (el.type||'') + '[' + (lab||'').slice(0,40) + ']=' + (el.value||'').slice(0,25)).slice(0,95));
            });
            return out;
          }""")}
    if extra: st.update(extra)
    json.dump(st, open(OUT, "w"), indent=1)
    print(f"[dump:{step}] {page.url[:70]}", flush=True)

def wait_cmd():
    while True:
        if os.path.exists(CMD):
            try:
                cmds = json.load(open(CMD)); os.remove(CMD); return cmds
            except Exception:
                time.sleep(0.5)
        time.sleep(0.7)

def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles", "wf_build"), executable_path=CLOAK, headless=False,
            args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
                  "--window-size=1500,950"])
        if os.path.exists(STATE):
            try: ctx.add_cookies(json.load(open(STATE)).get("cookies", []))
            except Exception: pass
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://wellfound.com/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        dump(page, "home")
        while True:
            cmds = wait_cmd()
            for c in cmds:
                if c.get("kind") == "close":
                    ctx.close(); return
                if c.get("kind") == "save_state":
                    json.dump({"cookies": ctx.cookies()}, open(STATE, "w"))
                    print(f"state saved: {len(ctx.cookies())} cookies -> {STATE}", flush=True)
                try:
                    if c["kind"] == "goto":
                        page.goto(c["url"], wait_until="domcontentloaded", timeout=45000)
                        page.wait_for_timeout(3000)
                    elif c["kind"] == "dump":
                        dump(page, c.get("note", "state"))
                    elif c["kind"] == "click":
                        if c.get("selector"):
                            page.click(c["selector"], timeout=4000)
                        else:
                            page.get_by_text(c["text"], exact=True).first.click(timeout=4000)
                        page.wait_for_timeout(1500)
                    elif c["kind"] == "fill":
                        if c.get("selector"):
                            page.fill(c["selector"], c["value"])
                        else:
                            page.get_by_placeholder(c["placeholder"]).first.fill(c["value"])
                    elif c["kind"] == "upload":
                        page.set_input_files(c["selector"], c["value"])
                        print("upload:", c["value"], flush=True)
                        page.wait_for_timeout(2500)
                    elif c["kind"] == "upload_btn":
                        with page.expect_file_chooser(timeout=8000) as fc:
                            page.click(c.get("selector", "button:has-text('Upload new file')"), timeout=5000)
                        fc.value.set_files(c["value"])
                        print("upload_btn:", c["value"], flush=True)
                        page.wait_for_timeout(3500)
                    elif c["kind"] == "js":
                        print("js:", page.evaluate(c["script"]), flush=True)
                    elif c["kind"] == "wait":
                        time.sleep(c.get("ms", 2000) / 1000)
                except Exception as e:
                    print("ACTION FAIL:", c, str(e)[:120], flush=True)
            dump(page, "after_cmds")

if __name__ == "__main__":
    main()
