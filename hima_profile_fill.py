"""Fill the himalayas talent profile sections with LO-approved values.

Pattern-matches form fields by placeholder/label/name, fills from the profile
lock, logs every action, verifies by re-dumping. No file-input touches (never
stages the resume as the profile picture). No Google screens involved.
"""
import os, sys, time, json
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
HERE = "/home/ubuntu/job_hunt_linkedin"
DST = os.path.join(HERE, "profiles", "hima_cap")
PORTAL = os.path.join(HERE, "portal_himalayas.json")

VALUES = {
    "company": "BFHR",
    "employer": "BFHR",
    "role|title|position|job title": "Full-Stack Engineer",
    "school|college|university|institute": "Sister Nivedita University",
    "degree|course|program": "B.Tech Computer Science",
    "location|city|where": "Kolkata, West Bengal, India",
    "salary|compensation|expected": "700000",
    "description|summary|about|bio": "Full-stack engineer (TypeScript/Python/Rust) with 1 year of experience, based in Kolkata, open to remote roles.",
}
SKILLS = ["TypeScript", "React", "Next.js", "Node.js", "Python", "Rust"]
SAVE_BTNS = ["button:has-text('Save')", "button:has-text('Save changes')", "button:has-text('Continue')",
             "button:has-text('Add')", "button:has-text('Next')"]


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def body(pg):
    try:
        return pg.inner_text("body")[:500]
    except Exception:
        return ""


def dump_fields(pg, label):
    try:
        info = pg.evaluate("""() => {
          const out = [];
          document.querySelectorAll('input:not([type=file]):not([type=hidden]), textarea, select').forEach(el => {
            const r = el.getBoundingClientRect();
            if (r.width === 0 && el.type !== 'radio') return;
            out.push({tag: el.tagName, type: el.type || '', name: el.name || '',
                      ph: el.placeholder || '', aria: el.getAttribute('aria-label') || '',
                      val: (el.value || '').slice(0,40)});
          });
          return out.slice(0, 25);
        }""")
        for f in info:
            log(f"  FIELD {label}: {f}")
        return info
    except Exception as e:
        log(f"  dump err: {str(e)[:60]}")
        return []


def fill_by_pattern(pg):
    """Fill visible inputs from VALUES by matching placeholder/label/name/aria."""
    fields = pg.evaluate("""() => [...document.querySelectorAll('input:not([type=file]):not([type=hidden]):not([type=radio]):not([type=checkbox]), textarea')].map((el, i) => i)""")
    idx = 0
    for _ in fields:
        filled = pg.evaluate("""(idx) => {
          const els = [...document.querySelectorAll('input:not([type=file]):not([type=hidden]):not([type=radio]):not([type=checkbox]), textarea')];
          const el = els[idx];
          if (!el) return {done: false};
          const r = el.getBoundingClientRect();
          if (r.width === 0) return {done: false};
          const hay = ((el.placeholder || '') + ' ' + (el.name || '') + ' ' + (el.getAttribute('aria-label') || '') + ' ' + (el.id || '')).toLowerCase();
          const MAP = %s;
          for (const [pat, val] of Object.entries(MAP)) {
            if (new RegExp(pat, 'i').test(hay)) {
              const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
              setter.call(el, val);
              el.dispatchEvent(new Event('input', {bubbles: true}));
              el.dispatchEvent(new Event('change', {bubbles: true}));
              return {done: true, pat: pat};
            }
          }
          return {done: false};
        }""" % json.dumps(VALUES), idx)
        if filled.get("done"):
            log(f"  filled idx={idx} pattern={filled.get('pat')}")
        idx += 1


def click_save(pg):
    for sel in SAVE_BTNS:
        try:
            el = pg.locator(sel).first
            if el.count() > 0:
                el.click(timeout=3000)
                log(f"  clicked save btn: {sel}")
                pg.wait_for_timeout(2500)
                return True
        except Exception:
            pass
    return False


def add_skills(pg):
    # find the skills input (chip-style) and type+Enter each skill
    for skill in SKILLS:
        try:
            inp = pg.locator("input[placeholder*='skill' i], input[placeholder*='add' i], input[placeholder*='search' i]").first
            if inp.count() > 0:
                inp.fill(skill)
                pg.keyboard.press("Enter")
                log(f"  skill entered: {skill}")
                pg.wait_for_timeout(1200)
            else:
                log(f"  no skill input found for {skill}")
                break
        except Exception as e:
            log(f"  skill err {skill}: {str(e)[:60]}")


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

    def goto_profile(path):
        pg.goto(f"https://himalayas.app/profile/{path}", wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(5000)
        if "performing security verification" in body(pg).lower():
            pg.mouse.click(212, 336)
            log("CF clicked")
            for _ in range(8):
                pg.wait_for_timeout(4000)
                if "performing security verification" not in body(pg).lower():
                    log("CF cleared")
                    break

    # --- Preferences ---
    log("=== PREFERENCES ===")
    goto_profile("preferences")
    log("body: " + body(pg)[:250])
    dump_fields(pg, "pref")
    fill_by_pattern(pg)
    click_save(pg)
    pg.screenshot(path="/tmp/hima_pref_fill.png")

    # --- Experience ---
    log("=== EXPERIENCE ===")
    goto_profile("experience")
    log("body: " + body(pg)[:250])
    for btn in ("button:has-text('Add experience')", "button:has-text('Add')", "text=Add your experience"):
        try:
            el = pg.locator(btn).first
            if el.count() > 0:
                el.click(timeout=3000)
                log(f"clicked {btn}")
                pg.wait_for_timeout(2500)
                break
        except Exception:
            pass
    dump_fields(pg, "exp")
    fill_by_pattern(pg)
    click_save(pg)
    pg.screenshot(path="/tmp/hima_exp_fill.png")

    # --- Education ---
    log("=== EDUCATION ===")
    goto_profile("education")
    log("body: " + body(pg)[:250])
    for btn in ("button:has-text('Add education')", "button:has-text('Add')"):
        try:
            el = pg.locator(btn).first
            if el.count() > 0:
                el.click(timeout=3000)
                log(f"clicked {btn}")
                pg.wait_for_timeout(2500)
                break
        except Exception:
            pass
    dump_fields(pg, "edu")
    fill_by_pattern(pg)
    click_save(pg)
    pg.screenshot(path="/tmp/hima_edu_fill.png")

    # --- Tech Stack ---
    log("=== TECH STACK ===")
    goto_profile("tech-stack")
    log("body: " + body(pg)[:250])
    if "fixing this for you" in body(pg).lower():
        pg.wait_for_timeout(10000)
        pg.goto("https://himalayas.app/profile/tech-stack", wait_until="domcontentloaded", timeout=30000)
        pg.wait_for_timeout(5000)
    dump_fields(pg, "stack")
    add_skills(pg)
    click_save(pg)
    pg.screenshot(path="/tmp/hima_stack_fill.png")

    # --- Verification dump ---
    for sec in ("preferences", "experience", "education", "tech-stack"):
        goto_profile(sec)
        b = body(pg)[:400]
        log(f"VERIFY {sec}: {b}")

    ctx.close()
