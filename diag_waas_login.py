import os, json, time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir="/tmp/diag_waas_login", executable_path=CLOAK, headless=True,
        args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
              "--window-size=1400,900"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        page.goto("https://www.workatastartup.com/login", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(6000)
    except Exception as e:
        print("goto err:", str(e)[:80])
    info = page.evaluate("""() => {
        const els = [...document.querySelectorAll('button, a, [role=button]')];
        return els.map(e => ({
            tag: e.tagName, role: e.getAttribute('role'),
            text: (e.innerText||'').trim().slice(0,40),
            href: (e.getAttribute('href')||'').slice(0,90)
        })).filter(x => x.text || x.href).slice(0, 25);
    }""")
    for x in info:
        print(x)
    print("URL:", page.url[:100])
    page.screenshot(path="/tmp/waas_login.png")
    ctx.close()
