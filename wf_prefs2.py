import json, os, time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright
HERE = "/home/ubuntu/Documents/job_hunt_linkedin"
STATE = os.path.join(HERE, "portal_wellfound.json")
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=os.path.join(HERE, "profiles", "prefs2_" + str(int(time.time()%100000))), executable_path="/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome", headless=True,
        args=["--no-first-run","--disable-blink-features=AutomationControlled","--window-size=1400,900"])
    ctx.add_cookies(json.load(open(STATE)).get("cookies", []))
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    for g in range(3):
        try:
            page.goto("https://wellfound.com/profile/edit/preferences", wait_until="domcontentloaded", timeout=40000)
            page.wait_for_timeout(5000); break
        except Exception: page.wait_for_timeout(3000)
    # location
    try:
        loc = page.locator("input[placeholder*='San Francisco']").first
        loc.fill("Kolkata, India")
        print("location set", flush=True)
    except Exception as e:
        print("loc fail", str(e)[:60], flush=True)
    # salary: find the salary text input (value looks like 75,000 / placeholder 70,000)
    try:
        sal = page.locator("input[placeholder*='70,000'], input[placeholder*='$'], input[placeholder*='salary']").first
        sal.fill("85,000")
        print("salary set", flush=True)
    except Exception as e:
        print("sal fail", str(e)[:60], flush=True)
    page.wait_for_timeout(2000)
    # dump current salary/location values
    vals = page.evaluate("""() => {
      const out = [];
      document.querySelectorAll('input').forEach(el => {
        const p = el.getAttribute('placeholder')||'';
        if (/San Francisco|70,000|salary/i.test(p) || /85/.test(el.value)) out.push(p.slice(0,30) + '=' + el.value);
      });
      return out;
    }""")
    print("VALUES:", vals[:6], flush=True)
    page.screenshot(path=os.path.join(HERE, "profiles", "prefs2.png"))
    ctx.close()
