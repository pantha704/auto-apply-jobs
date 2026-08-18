"""Probe the himalayas apply flow with the live session."""
import os, sys, time, json
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
HERE = "/home/ubuntu/job_hunt_linkedin"
DST = os.path.join(HERE, "profiles", "hima_cap")
PORTAL = os.path.join(HERE, "portal_himalayas.json")
URL = os.environ.get("JOB_URL", "https://himalayas.app/companies/valce-talent-solutions/jobs/software-engineer")


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def ensure_loaded(pg):
    for _ in range(14):
        try:
            b = pg.inner_text("body")
        except Exception:
            pg.wait_for_timeout(3000)
            continue
        low = b.lower()
        if "performing security verification" in low or "verify you are human" in low:
            pg.mouse.click(212, 336)
            log("CF clicked")
            pg.wait_for_timeout(5000)
            continue
        return True
    return False


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
    pg.goto(URL, wait_until="domcontentloaded", timeout=45000)
    ensure_loaded(pg)
    log(f"url: {pg.url[:100]}")
    b = pg.inner_text("body")
    log("body head: " + b[:500].replace("\n", " | "))
    pg.screenshot(path="/tmp/hima_job_page.png")

    # find apply button
    btns = pg.evaluate("""() => [...document.querySelectorAll('a, button')].map(e => ({
        tag: e.tagName, txt: (e.innerText||'').trim().slice(0,50), href: e.href ? e.href.slice(0,120) : ''
    })).filter(x => /apply|apply now/i.test(x.txt) || /apply/i.test(x.href))""")
    log("apply elements: " + json.dumps(btns))

    # click it — try each Apply now button until a dialog opens
    for i in range(3):
        try:
            el = pg.locator("button:has-text('Apply now')").nth(i)
            if el.count() > 0:
                el.click(timeout=3000)
                pg.wait_for_timeout(3500)
                dlg = pg.evaluate("""() => {
                  const d = document.querySelector('[role=dialog], [aria-modal=true]');
                  if (!d) return null;
                  const r = d.getBoundingClientRect();
                  return r.width > 0 ? (d.innerText||'').slice(0, 120) : null;
                }""")
                if dlg:
                    log(f"button[{i}] opened dialog: {dlg[:100]}")
                    clicked = i
                    break
                else:
                    log(f"button[{i}] no dialog")
        except Exception as e:
            log(f"button[{i}] err: {str(e)[:60]}")
    pg.wait_for_timeout(4000)
    ensure_loaded(pg)
    log(f"after click url: {pg.url[:100]}")
    b2 = pg.inner_text("body")
    log("after click body: " + b2[:700].replace("\n", " | "))
    pg.screenshot(path="/tmp/hima_apply_modal.png")

    # click through the AI upsell interstitial if present
    low = b2.lower()
    if "i'm ready to apply" in low or "ready to apply" in low:
        try:
            # also click "Don't show this again" first so future applies skip it
            try:
                pg.click("text=Don't show this again", timeout=2500)
                log("clicked 'Don't show this again'")
                pg.wait_for_timeout(800)
            except Exception:
                pass
            pg.click("button:has-text(\"I'm ready to apply\")", timeout=4000)
            log("clicked I'm ready to apply")
            pg.wait_for_timeout(4000)
            ensure_loaded(pg)
            log(f"post-interstitial url: {pg.url[:100]}")
            b2 = pg.inner_text("body")
            log("post-interstitial body: " + b2[:800].replace("\n", " | "))
            pg.screenshot(path="/tmp/hima_apply_form.png")
        except Exception as e:
            log(f"interstitial click err: {str(e)[:60]}")

    # dump any form fields
    fields = pg.evaluate("""() => {
      const out = [];
      document.querySelectorAll('input:not([type=file]):not([type=hidden]), textarea, select').forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.width === 0) return;
        out.push({tag: el.tagName, type: el.type||'', ph: el.placeholder||'', name: el.name||'',
                  val: (el.value||'').slice(0,40)});
      });
      return out.slice(0, 20);
    }""")
    for f in fields:
        log("FIELD: " + json.dumps(f))
    ctx.close()
