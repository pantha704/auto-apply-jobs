"""hima fill v9 — FINAL: company via 'Add BFHR', country via suggestion, full save."""
import os, sys, time, json
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
HERE = "/home/ubuntu/job_hunt_linkedin"
DST = os.path.join(HERE, "profiles", "hima_cap")
PORTAL = os.path.join(HERE, "portal_himalayas.json")

DESC = ("Full-stack engineer (TypeScript/Python/Rust) with 1 year of experience, "
        "based in Kolkata, open to remote roles.")


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


def add_autocomplete(pg, ph, text):
    """Type into an autocomplete and click its 'Add <text>' suggestion."""
    try:
        inp = pg.locator(f"input[placeholder='{ph}']").first
        inp.click()
        pg.keyboard.type(text, delay=100)
        pg.wait_for_timeout(1400)
        r = pg.evaluate("""(t) => {
          const els = [...document.querySelectorAll('[role=option], li, [class*=suggest], [class*=menu], [class*=dropdown]')].filter(e => {
            const x = (e.innerText||'').toLowerCase();
            const b = e.getBoundingClientRect();
            return b.width > 0 && (x.includes(t.toLowerCase()) || x.includes('add ') || x.includes('create') || x.startsWith('use '));
          });
          if (els[0]) { els[0].click(); return (els[0].innerText||'').trim().slice(0,40); }
          return 'none';
        }""", text)
        log(f"autocomplete {ph}: clicked {r!r}")
        return r != "none"
    except Exception as e:
        log(f"autocomplete {ph} err: {str(e)[:60]}")
        return False


with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=DST, executable_path=CLOAK, headless=True,
        args=["--no-first-run", "--no-default-browser-check",
              "--disable-blink-features=AutomationControlled", "--window-size=1280,720"])
    ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    try:
        ctx.add_cookies(json.load(open(PORTAL)).get("cookies", []))
    except Exception:
        pass
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    pg.set_default_timeout(15000)

    log("=== EXPERIENCE v9 (final) ===")
    pg.goto("https://himalayas.app/profile/experience", wait_until="domcontentloaded", timeout=45000)
    ensure_loaded(pg)
    pg.click("button:has-text('Add experience')", timeout=6000)
    pg.wait_for_timeout(3000)
    ensure_loaded(pg)

    try:
        pg.locator("input[name=title]").first.fill("Full-Stack Engineer")
        pg.locator("textarea[name=description]").first.fill(DESC)
        log("title + description filled")
    except Exception as e:
        log(f"title/desc err: {str(e)[:60]}")
    pg.wait_for_timeout(500)

    add_autocomplete(pg, "e.g. Himalayas", "BFHR")
    pg.wait_for_timeout(600)
    add_autocomplete(pg, "Search for a country", "India")
    pg.wait_for_timeout(600)

    # current-role checkbox
    r = pg.evaluate("""() => {
      const cb = [...document.querySelectorAll('input[type=checkbox]')].find(c =>
        (c.closest('label, div, section')?.innerText || '').toLowerCase().includes('currently working'));
      if (cb && !cb.checked) { cb.click(); return 'checked'; }
      if (cb) return 'already-on';
      return 'not-found';
    }""")
    log(f"current-role: {r}")
    pg.wait_for_timeout(1200)

    # start date April 2026
    r = pg.evaluate("""() => {
      const m = [...document.querySelectorAll('button[role=combobox]')].find(b => (b.innerText||'').trim() === 'Month');
      if (m) { m.scrollIntoView({block:'center'}); m.click(); return 'ok'; }
      return 'no';
    }""")
    log(f"open start month: {r}")
    pg.wait_for_timeout(900)
    r = pg.evaluate("""() => {
      const el = [...document.querySelectorAll('*')].find(e => (e.innerText||'').trim() === 'April'
        && e.children.length === 0 && e.getBoundingClientRect().width > 0);
      if (el) { el.click(); return 'ok'; }
      return 'no';
    }""")
    log(f"pick April: {r}")
    pg.wait_for_timeout(800)
    r = pg.evaluate("""() => {
      const y = [...document.querySelectorAll('button[role=combobox]')].find(b => (b.innerText||'').trim() === 'Year');
      if (y) { y.scrollIntoView({block:'center'}); y.click(); return 'ok'; }
      return 'no';
    }""")
    log(f"open start year: {r}")
    pg.wait_for_timeout(900)
    r = pg.evaluate("""() => {
      const el = [...document.querySelectorAll('*')].find(e => (e.innerText||'').trim() === '2026'
        && e.children.length === 0 && e.getBoundingClientRect().width > 0);
      if (el) { el.click(); return 'ok'; }
      return 'no';
    }""")
    log(f"pick 2026: {r}")
    pg.wait_for_timeout(1200)

    pg.screenshot(path="/tmp/hima_v9_before_submit.png")
    r = pg.evaluate("""() => {
      const modal = document.querySelector('[aria-modal=true], [role=dialog]') || document.body;
      const btns = [...modal.querySelectorAll('button')];
      const el = btns[btns.length - 1];
      if (el) { el.scrollIntoView({block:'center'}); el.click(); return (el.innerText||'').trim().slice(0,30); }
      return 'no';
    }""")
    log(f"submit clicked: {r}")
    pg.wait_for_timeout(6000)
    ensure_loaded(pg)
    b = pg.inner_text("body")
    log(f"exp verify: BFHR={'BFHR' in b} FSE={'Full-Stack Engineer' in b}")
    pg.screenshot(path="/tmp/hima_v9_after.png")
    ctx.close()
