#!/usr/bin/env python3
"""Autonomous LinkedIn Easy-Apply worker. Claims jobs from SQLite queue atomically.
Usage: python3 worker_linkedin.py <worker_id>
"""
import json, os, re, sqlite3, sys, time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright
import audit
from worker_guard import BrowserWatchdog
import profile as ident

HERE = os.path.dirname(os.path.abspath(__file__))
CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
STATE = os.path.join(HERE, "li_state.json")
RESUME = "/home/ubuntu/Documents/Pratham_Jaiswal_Updated_Resume.pdf"
WORKER_ID = sys.argv[1] if len(sys.argv) > 1 else "li-w1"
STEALTH = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

PROFILE_DATA = {
    "phone": ident.PHONE,
    "address": ident.ADDRESS,
    "city": ident.CITY, "state": ident.STATE, "pin": ident.PIN,
    "linkedin": "https://www.linkedin.com/in/pantha704",
    "portfolio": "https://pantha704.github.io",
    "college": ident.COLLEGE,
    "expected": "700000", "current": "480000", "notice": "0",
    "stack": "TypeScript, JavaScript, Python, Rust, Node.js, Next.js, React, Tailwind CSS, PostgreSQL, Prisma, Redis, Docker, Kubernetes, Solana/Anchor, REST APIs, WebSockets",
    "pitch": "Full-stack & Solana engineer in Turbin3 cohort; 4 merged OSS PRs (Rust, PyTorch, DeepMind, CircuitVerse); shipped AI crawler, DeFi credit scoring, RWA platform.",
}
ZERO_TOPICS = ["go", "banking", "php", "laravel", "java", "excel", "wordpress", "django", "embedded", "iot", "excel"]
ONE_TOPICS = ["python", "typescript", "react", "node", "javascript", "database", "postgres", "sql",
              "full stack", "frontend", "backend", "software", "development", "information technology", "it "]

def db():
    return sqlite3.connect(os.path.join(HERE, "apply_queue.db"))

def claim(portal):
    c = db()
    row = c.execute("SELECT id, url, title FROM jobs WHERE portal=? AND status='pending' ORDER BY prio DESC, rowid LIMIT 1", (portal,)).fetchone()
    if not row:
        c.close(); return None
    upd = c.execute("UPDATE jobs SET status='claimed', claimed_by=? WHERE id=? AND status='pending'", (WORKER_ID, row[0]))
    c.commit(); c.close()
    if upd.rowcount != 1:
        return claim(portal)  # lost race, next
    return {"id": row[0], "url": row[1], "title": row[2]}

def mark(jid, status, result=""):
    c = db()
    c.execute("UPDATE jobs SET status=?, result=? WHERE id=?", (status, result[:200], jid))
    c.commit(); c.close()

def log(msg):
    print(f"[{WORKER_ID}] {msg}", flush=True)

# ---------- JS helpers ----------
def js_text(page, q, value):
    return page.evaluate(f"""(() => {{
      const q = {json.dumps(q.lower())}; const V = {json.dumps(value)};
      let el = [...document.querySelectorAll('input,select,textarea')].find(e => (e.getAttribute('aria-label')||'').toLowerCase().includes(q));
      if (!el) {{ const lab = [...document.querySelectorAll('label')].find(e => (e.innerText||'').toLowerCase().includes(q)); if (lab && lab.htmlFor) el = document.getElementById(lab.htmlFor); }}
      if (!el) {{ const leaf = [...document.querySelectorAll('p,span')].find(e => e.children.length===0 && (e.innerText||'').toLowerCase().includes(q)); let p = leaf ? leaf.parentElement : null; for (let i=0;i<3 && p;i++) {{ const c = p.querySelector('input,select,textarea'); if (c) {{ el = c; break; }} p = p.parentElement; }} }}
      if (!el) return false;
      if (el.tagName === 'SELECT') {{
        const opt = [...el.options].find(o => (o.text||'').trim() === V);
        if (!opt) return false;
        el.value = opt.value; el.dispatchEvent(new Event('change', {{bubbles:true}})); return true;
      }}
      const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, V);
      el.dispatchEvent(new Event('input', {{bubbles:true}})); el.dispatchEvent(new Event('change', {{bubbles:true}}));
      return true;
    }})()""")

def js_radio(page, q, choice):
    return page.evaluate(f"""(() => {{
      const q = {json.dumps(q.lower())}; const choice = {json.dumps(choice.lower())};
      const leaves = [...document.querySelectorAll('p,span,label')].filter(e => e.children.length===0 && (e.innerText||'').toLowerCase().includes(q));
      if (!leaves.length) return false;
      leaves.sort((a,b) => (a.innerText||'').length - (b.innerText||'').length);
      const leaf = leaves[0];
      let p = leaf.parentElement; let scope = null;
      for (let i=0;i<6 && p;i++){{ if (p.querySelectorAll('[role=radio]').length) {{ scope = p; break; }} p = p.parentElement; }}
      if (!scope) return false;
      const r = [...scope.querySelectorAll('[role=radio]')].find(x => (x.getAttribute('aria-label')||'').trim().toLowerCase() === choice);
      if (!r) return false;
      r.click(); return true;
    }})()""")

def field_map(page):
    """Return dict label_lower -> element info for current page."""
    return page.evaluate("""() => {
      const out = {};
      for (const el of document.querySelectorAll('input,select,textarea')) {
        let lab = el.getAttribute('aria-label') || '';
        if (!lab && el.id) { const l = document.querySelector(`label[for="${el.id}"]`); if (l) lab = l.innerText; }
        if (!lab) { const wrap = el.closest('.artdeco-text-input--container, .fb-dash-form-element'); if (wrap) lab = (wrap.innerText||'').replace(el.value||'',''); }
        lab = (lab||'').trim().toLowerCase().slice(0,60);
        if (lab && lab !== 'search' && lab !== 'select language') {
          out[lab] = {type: el.type||'', tag: el.tagName, required: !!el.required, value: el.value||''};
        }
      }
      return out;
    }""")

def answer_page(page):
    """Auto-answer screening questions on current page. Returns count answered."""
    fields = field_map(page)
    n = 0
    for lab, f in fields.items():
        low = lab
        # selects with Yes/No
        if f["tag"] == "SELECT" and ("yes" in f["value"] or f["value"] == ""):
            opts = page.evaluate(f"""() => {{
              const lab = {json.dumps(lab)};
              const el = [...document.querySelectorAll('select')].find(e => ((e.getAttribute('aria-label')||'').toLowerCase().startsWith(lab) || e.id && (document.querySelector('label[for="'+e.id+'"]')?.innerText||'').toLowerCase().startsWith(lab)));
              return el ? [...el.options].map(o=>o.text.trim()) : [];
            }}""")
            if opts and "Yes" in opts and "No" in opts:
                choice = "No" if any(k in low for k in ["relocat", "commut", "bangalore", "current", "located near"]) else ("Yes" if any(k in low for k in ["onsite", "office", "internet", "18", "work"] ) else "No")
                if "education" in low or "degree" in low: choice = "No"
                if "start" in low or "notice" in low: choice = None
                if choice and js_text(page, lab, choice): n += 1
        if f["tag"] in ("INPUT", "TEXTAREA") and f["type"] in ("text", "tel", ""):
            if any(k in low for k in ["experience", "years", "salary", "ctc", "notice", "phone", "postal", "pin", "date", "start", "address", "city", "state", "gpa", "skills", "technolog", "framework", "unique", "url", "portfolio", "college", "university", "company", "title", "location", "lwd"]):
                val = None
                if "salary" in low or "ctc" in low:
                    val = PROFILE_DATA["expected"] if "expected" in low else PROFILE_DATA["current"]
                elif "notice" in low or "lwd" in low:
                    val = PROFILE_DATA["notice"]
                elif "phone" in low:
                    val = PROFILE_DATA["phone"]
                elif "address" in low: val = PROFILE_DATA["address"]
                elif "city" in low: val = PROFILE_DATA["city"]
                elif "state" in low: val = PROFILE_DATA["state"]
                elif "postal" in low or "pin" in low: val = PROFILE_DATA["pin"]
                elif "linkedin" in low: val = PROFILE_DATA["linkedin"]
                elif "portfolio" in low or "website" in low: val = PROFILE_DATA["portfolio"]
                elif "college" in low or "university" in low: val = PROFILE_DATA["college"]
                elif "technolog" in low: val = PROFILE_DATA["stack"]
                elif "framework" in low: val = "Next.js, React, Node.js, Hono"
                elif "unique" in low: val = PROFILE_DATA["pitch"]
                elif "start" in low: val = "Immediate"
                elif "skills" in low: val = "4"
                elif "gpa" in low: val = ""  # skip GPA
                elif "experience" in low or "years" in low:
                    if any(k in low for k in ZERO_TOPICS): val = "0"
                    elif any(k in low for k in ONE_TOPICS): val = "1"
                    else: val = "1"
                if val is not None and f["value"] == "":
                    if js_text(page, lab, val): n += 1
    return n

def main():
    while True:
        job = claim("linkedin")
        if not job:
            log("queue empty, sleeping")
            time.sleep(60)
            continue
        log(f"claim: {job['title'][:60]}")
        GUARD = BrowserWatchdog(f"li_w_{WORKER_ID}", max_sec=240, job=(job["id"], job["url"]))
        GUARD.start()
        try:
            ok = apply_job(job["url"])
        except Exception as e:
            if GUARD.fired.is_set():
                mark(job["id"], "pending", "browser-wedge-timeout")
                log("BROWSER WEDGE — job requeued, exiting for systemd restart")
                os._exit(7)
            mark(job["id"], "error", str(e)[:150])
            log(f"ERROR: {str(e)[:120]}")
            GUARD.stop(); time.sleep(2); continue
        GUARD.stop()
        if GUARD.fired.is_set():
            mark(job["id"], "pending", "browser-wedge-timeout")
            log("BROWSER WEDGE — job requeued, exiting for systemd restart")
            os._exit(7)
        mark(job["id"], "done" if ok[0] else "skip", ok[1])
        try:
            audit.record_application(
                portal="linkedin", company=job.get("company") or "(linkedin)", role=job["title"],
                url=job["url"], status="submitted" if ok[0] else f"skipped:{ok[1][:60]}",
                answers=getattr(apply_job, "last_answers", None),
                resume_used=os.path.basename(RESUME), note=ok[1][:200])
            log(f"{'DONE' if ok[0] else 'SKIP'}: {job['title'][:50]} ({ok[1][:60]})")
        except Exception as e:
            mark(job["id"], "error", str(e)[:150])
            log(f"ERROR: {str(e)[:120]}")
        time.sleep(2)

def apply_job(url):
    apply_job.last_answers = {"phone": PROFILE_DATA["phone"], "address": PROFILE_DATA["address"],
                              "city": PROFILE_DATA["city"], "state": PROFILE_DATA["state"],
                              "pin": PROFILE_DATA["pin"], "college": PROFILE_DATA["college"],
                              "linkedin": PROFILE_DATA["linkedin"], "portfolio": PROFILE_DATA["portfolio"]}
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=f"/tmp/li_w_{WORKER_ID}", executable_path=CLOAK, headless=True,
            args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
                  "--window-size=1400,900"])
        if os.path.exists(STATE):
            try:
                ctx.add_cookies(json.load(open(STATE)).get("cookies", []))
            except Exception as e:
                log(f"cookie inject fail: {str(e)[:80]}")
        ctx.add_init_script(STEALTH)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            for gtry in range(3):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    break
                except Exception:
                    if gtry == 2:
                        return (False, "goto-fail")
                    page.wait_for_timeout(4000)
            page.wait_for_timeout(2500)
            txt = page.inner_text("body")
            try:
                page.locator("button:has-text('Easy Apply')").first.wait_for(state="visible", timeout=12000)
                is_easy = True
            except Exception:
                is_easy = False
            if not is_easy:
                if "Responses managed off LinkedIn" in txt or "No longer accepting" in txt or "no longer accepting" in txt:
                    return (False, "external-or-closed")
                return (False, "no-easy-apply")

            modal_open = False
            for attempt in range(3):
                try:
                    btn = page.locator("button:has-text('Easy Apply')").first
                    btn.scroll_into_view_if_needed(timeout=3000)
                    btn.click(timeout=4000)
                    page.wait_for_timeout(2000)
                except Exception:
                    pass
                modal_open = "Apply to" in page.inner_text("body")[:600] or "Contact info" in page.inner_text("body")[:600]
                if modal_open:
                    break
            if not modal_open:
                return (False, "modal-not-opened")
            # single-page form detection: Submit application present, no Next
            has_submit = page.locator("button:has-text('Submit application')").count() > 0
            has_next = page.locator("button:has-text('Next')").count() > 0
            if has_submit and not has_next:
                # ensure resume selected
                page.evaluate("""() => {
                  const rs=[...document.querySelectorAll('input[type=radio]')];
                  if (rs.some(r=>r.checked)) return 'ok';
                  const t=rs.find(r=>{let p=r.parentElement;for(let i=0;i<5&&p;i++){if((p.innerText||'').includes('Pratham_Jaiswal_Updated_Resume'))return true;p=p.parentElement;}return false;});
                  if (t) t.click(); else if (rs.length) rs[0].click();
                  return 'selected';
                }""")
                page.wait_for_timeout(800)
                try:
                    audit.snapshot(page, "linkedin", url, "before")
                except Exception:
                    pass
                click_any(page, ["^Submit application$"])
                page.wait_for_timeout(3500)
                try:
                    audit.snapshot(page, "linkedin", url, "after")
                except Exception:
                    pass
                body = page.inner_text("body")
                ok = bool(re.search(r"Application submitted|Your application was sent", body, re.I))
                return (ok, "single-page-submitted" if ok else "single-page-uncertain|" + body[:120].replace("\n", " | "))
            # multi-page: fill phone + address, then advance
            # page 1: contact — fill phone if empty
            try:
                for ph in page.query_selector_all("input[type=tel]"):
                    if not ph.input_value():
                        ph.fill(PROFILE_DATA["phone"])
            except Exception:
                pass
            # address/city/state/postal if present and empty
            for lab in ["Address", "City", "State", "Postal", "Postal Code"]:
                try:
                    loc = page.locator(f"input[aria-label*='{lab}']")
                    if loc.count():
                        v = loc.first.input_value()
                        if not v:
                            val = {"Address": PROFILE_DATA["address"], "City": PROFILE_DATA["city"],
                                   "State": PROFILE_DATA["state"], "Postal": PROFILE_DATA["pin"], "Postal Code": PROFILE_DATA["pin"]}[lab]
                            loc.first.fill(val)
                except Exception:
                    pass
            advance(page, "Next", "contact")
            # resume page
            advance(page, "Next", "resume1")
            resume_ok = False
            for sel_attempt in range(4):
                page.evaluate("""() => {
                  const rs=[...document.querySelectorAll('input[type=radio]')];
                  if (rs.some(r=>r.checked)) return 'checked-ok';
                  const t=rs.find(r=>{let p=r.parentElement;for(let i=0;i<6&&p;i++){if((p.innerText||'').includes('Pratham_Jaiswal_Updated_Resume'))return true;p=p.parentElement;}return false;});
                  if (t) { t.click(); t.dispatchEvent(new Event('change',{bubbles:true})); const lab=t.closest('label'); if (lab) lab.click(); return 'clicked-ours'; }
                  if (rs.length) { rs[0].click(); return 'clicked-first'; }
                  return 'none';
                }""")
                page.wait_for_timeout(1300)
                en = page.evaluate("""() => {
                  const b=[...document.querySelectorAll('button')].find(x=>/^Next$/i.test((x.innerText||'').trim()));
                  return b ? {disabled: !!b.disabled} : {missing: true};
                }""")
                if not en.get("missing") and not en.get("disabled"):
                    resume_ok = True
                    break
            if not resume_ok:
                diag = page.evaluate("""() => [...document.querySelectorAll('button')].map(b=>((b.innerText||'').trim().slice(0,22)+':'+(b.disabled?1:0))).filter(x=>x.includes('Next')||x.includes('Review')).join(' | ') || 'no-next'""")
                return (False, "resume-stuck|" + diag[:100])
            click_any(page, ["^Next$", "^Review$"])
            if "Resume*" in page.inner_text("body")[:400]:
                click_any(page, ["^Next$", "^Review$"])
            # work-experience page: add entry if required
            try:
                if page.get_by_role("button", name=re.compile("^Add work experience$", re.I)).count():
                    page.get_by_role("button", name=re.compile("^Add work experience$", re.I)).click(timeout=4000)
                    page.wait_for_timeout(1500)
                    for q, v in [("Your title", "Full-Stack Developer"), ("Company", "Braid-Forbes Health Research")]:
                        js_text(page, q, v)
                    page.evaluate("""() => {
                      const mod = document.querySelector('.jobs-easy-apply-modal, [role=dialog]') || document.body;
                      const sels = [...mod.querySelectorAll('select')];
                      if (sels[0]) { const o = [...sels[0].options].find(x=>x.text.trim()==='April'); if (o) { sels[0].value=o.value; sels[0].dispatchEvent(new Event('change',{bubbles:true})); } }
                      if (sels[1]) { const o = [...sels[1].options].find(x=>x.text.trim()==='2026'); if (o) { sels[1].value=o.value; sels[1].dispatchEvent(new Event('change',{bubbles:true})); } }
                      const cb = [...document.querySelectorAll('[role=checkbox]')].find(e => /currently work here/i.test(e.innerText||''));
                      if (cb && cb.getAttribute('aria-checked') !== 'true') cb.click();
                      return 'we-filled';
                    }""")
                    page.wait_for_timeout(800)
                    click_any(page, ["^Save$"])
                    page.wait_for_timeout(1500)
            except Exception:
                pass
            # screening pages — auto answer, up to 6 pages
            for pg in range(6):
                body = page.inner_text("body")
                if "Submit application" in body and "Review" not in body.split("Submit application")[0]:
                    pass
                ans = answer_page(page)
                if "Review" in page.inner_text("body"):
                    break
                # find next/review button
                if not click_any(page, ["^Review$", "^Next$"]):
                    return (False, "stuck-no-next")
                page.wait_for_timeout(1800)
            # review → verify errors → submit
            errs = page.evaluate("""() => { let n=0; for (const el of document.querySelectorAll('input,select,textarea')) { const hd=el.getAttribute('aria-describedby'); if (hd){ const h=document.getElementById(hd); if (h && (h.innerText||'').includes('required')) n++; } } return n; }""")
            if errs:
                # one more answer pass
                answer_page(page)
            if not click_any(page, ["^Submit application$"]):
                try:
                    diag = page.inner_text("body")[:250].replace("\n", " | ")
                except Exception:
                    diag = "?"
                return (False, "no-submit-btn|" + diag)
            try:
                audit.snapshot(page, "linkedin", url, "before")
            except Exception:
                pass
            page.wait_for_timeout(4000)
            try:
                audit.snapshot(page, "linkedin", url, "after")
            except Exception:
                pass
            body = page.inner_text("body")
            if re.search(r"Application submitted|Your application was sent", body, re.I):
                return (True, "submitted")
            return (False, "submit-uncertain|" + body[:150].replace("\n", " | "))
        except Exception as e:
            return (False, str(e)[:100])
        finally:
            try: ctx.close()
            except Exception: pass

def advance(page, label, note):
    click_any(page, [f"^{label}$"])

def click_any(page, labels):
    for lab in labels:
        try:
            page.get_by_role("button", name=re.compile(lab, re.I)).first.click(timeout=3000)
            page.wait_for_timeout(1000)
            return True
        except Exception:
            continue
    return False

if __name__ == "__main__":
    main()
