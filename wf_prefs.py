#!/usr/bin/env python3
"""Set Wellfound profile preferences (salary, remote, location)."""
import json, os, time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright
HERE = "/home/ubuntu/Documents/job_hunt_linkedin"
STATE = os.path.join(HERE, "portal_wellfound.json")
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=os.path.join(HERE, "profiles", "prefs_" + str(int(time.time()%100000))), executable_path="/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome", headless=True,
        args=["--no-first-run","--disable-blink-features=AutomationControlled","--window-size=1400,900"])
    ctx.add_cookies(json.load(open(STATE)).get("cookies", []))
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    for g in range(3):
        try:
            page.goto("https://wellfound.com/profile/edit/preferences", wait_until="domcontentloaded", timeout=40000)
            page.wait_for_timeout(5000); break
        except Exception: page.wait_for_timeout(3000)
    print("URL:", page.url[:60], flush=True)
    # dump fields
    info = page.evaluate("""() => {
      const out = [];
      document.querySelectorAll('input, select, textarea, button').forEach(el => {
        let lab = el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
        if (!lab && el.id) { const l = document.querySelector(`label[for="${el.id}"]`); if (l) lab = l.innerText; }
        if (el.tagName === 'BUTTON') { const t = (el.innerText||'').trim(); if (t && /edit|add|save|salary|remote/i.test(t)) out.push('BTN:' + t.slice(0,30)); return; }
        out.push((el.tagName+'.'+(el.type||'')+'['+(lab||'').slice(0,40)+']='+(el.value||'').slice(0,25)).slice(0,95));
      });
      return out;
    }""")
    for f in info[:40]: print("  ", f, flush=True)
    page.screenshot(path=os.path.join(HERE, "profiles", "prefs.png"))
    ctx.close()
