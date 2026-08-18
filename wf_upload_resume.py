#!/usr/bin/env python3
"""One-shot: upload updated resume to Wellfound (headed)."""
from playwright.sync_api import sync_playwright
import json, os
import os,sys,time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
HERE = os.path.dirname(os.path.abspath(__file__))
CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
STATE = os.path.join(HERE, "portal_wellfound.json")
RESUME = "/home/ubuntu/Documents/Pratham_Jaiswal_Updated_Resume.pdf"

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=os.path.join(HERE, "profiles", "wf_upload_" + str(int(time.time() % 100000))), executable_path=CLOAK, headless=False,
        args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
              "--window-size=1500,950"])
    if os.path.exists(STATE):
        try: ctx.add_cookies(json.load(open(STATE)).get("cookies", []))
        except Exception: pass
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    for g in range(3):
        try:
            page.goto("https://wellfound.com/profile/edit/resume", wait_until="domcontentloaded", timeout=40000)
            page.wait_for_timeout(4000)
            break
        except Exception as e:
            print("goto retry", g, str(e)[:60], flush=True)
            page.wait_for_timeout(3000)
    print("url:", page.url[:60], flush=True)
    # try file-chooser via Upload new file button
    done = False
    for sel in ["button:has-text('Upload new file')", "a:has-text('Upload new file')", "text=Upload new file"]:
        try:
            with page.expect_file_chooser(timeout=8000) as fc:
                page.click(sel, timeout=5000)
            fc.value.set_files(RESUME)
            print("uploaded via:", sel, flush=True)
            done = True
            break
        except Exception as e:
            print("fail", sel, str(e)[:60], flush=True)
    page.wait_for_timeout(5000)
    t = page.inner_text("body")
    print("new resume visible:", "Pratham" in t and ".pdf" in t, flush=True)
    i = t.find("Upload your most up-to-date")
    print("SECTION:", t[i:i+300].replace("\n", " | ") if i > 0 else t[:200], flush=True)
    ctx.close()
