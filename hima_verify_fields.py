"""Verify what values are currently in the hima profile section forms (read-only)."""
import os, sys, time, json
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
HERE = "/home/ubuntu/job_hunt_linkedin"
DST = os.path.join(HERE, "profiles", "hima_cap")
PORTAL = os.path.join(HERE, "portal_himalayas.json")


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=DST, executable_path=CLOAK, headless=False,
        args=["--no-first-run", "--no-default-browser-check",
              "--disable-blink-features=AutomationControlled", "--window-size=1280,720"])
    ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    try:
        ctx.add_cookies(json.load(open(PORTAL)).get("cookies", []))
    except Exception:
        pass
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    pg.set_default_timeout(15000)

    for sec in ("preferences", "experience", "education", "tech-stack"):
        pg.goto(f"https://himalayas.app/profile/{sec}", wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(5000)
        if "performing security verification" in pg.inner_text("body").lower():
            pg.mouse.click(212, 336)
            for _ in range(8):
                pg.wait_for_timeout(4000)
                if "performing security verification" not in pg.inner_text("body").lower():
                    break
        fields = pg.evaluate("""() => {
          const out = [];
          document.querySelectorAll('input:not([type=file]):not([type=hidden]), textarea, select').forEach(el => {
            const r = el.getBoundingClientRect();
            if (r.width === 0) return;
            let v = el.value || '';
            if (el.tagName === 'SELECT' && el.selectedIndex >= 0) v = el.options[el.selectedIndex].text;
            out.push({tag: el.tagName, type: el.type || '', ph: el.placeholder || '',
                      name: el.name || '', aria: (el.getAttribute('aria-label')||'').slice(0,30), val: v.slice(0,50)});
          });
          return out.slice(0, 20);
        }""")
        log(f"=== {sec} ===")
        for f in fields:
            log(f"  {f}")
        pg.screenshot(path=f"/tmp/hima_verify_{sec}.png")
    ctx.close()
