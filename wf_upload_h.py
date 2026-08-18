#!/usr/bin/env python3
"""Headless CloakBrowser: upload updated resume via file-chooser pattern."""
import json, os, time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright
HERE = os.path.dirname(os.path.abspath(__file__))
CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
STATE = os.path.join(HERE, "portal_wellfound.json")
RESUME = "/home/ubuntu/Documents/Pratham_Jaiswal_Updated_Resume.pdf"
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=os.path.join(HERE, "profiles", "wfu_" + str(int(time.time()%100000))), executable_path=CLOAK, headless=True,
        args=["--no-first-run","--disable-blink-features=AutomationControlled","--window-size=1400,900"])
    ctx.add_cookies(json.load(open(STATE)).get("cookies", []))
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    for g in range(3):
        try:
            page.goto("https://wellfound.com/profile/edit/resume", wait_until="domcontentloaded", timeout=40000)
            page.wait_for_timeout(5000); break
        except Exception as e:
            print("goto retry", g, str(e)[:50], flush=True); page.wait_for_timeout(3000)
    t0 = page.inner_text("body")
    print("before:", "about 1 year old" in t0, flush=True)
    done = False
    # 1) file chooser via button
    try:
        with page.expect_file_chooser(timeout=8000) as fc:
            page.click("button:has-text('Upload new file')", timeout=5000)
        fc.value.set_files(RESUME)
        print("chooser upload ok", flush=True); done = True
    except Exception as e:
        print("chooser fail:", str(e)[:60], flush=True)
    if not done:
        try:
            page.locator("input[type=file]").first.set_input_files(RESUME)
            print("direct set ok", flush=True); done = True
        except Exception as e:
            print("direct fail:", str(e)[:60], flush=True)
    for i in range(8):
        page.wait_for_timeout(4000)
        t = page.inner_text("body")
        if "about 1 year old" not in t:
            print(f"UPLOADED (state changed at {i*4+4}s)", flush=True); break
    t = page.inner_text("body")
    i = t.find("Upload your most up-to-date")
    print("AFTER:", t[i:i+250].replace("\n"," | ") if i > 0 else t[:200], flush=True)
    page.screenshot(path=os.path.join(HERE, "profiles", "resume_state.png"))
    print("screenshot saved", flush=True)
    ctx.close()
