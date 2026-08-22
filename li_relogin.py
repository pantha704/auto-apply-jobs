import atexit, os, time, re, socket
import urllib.parse as up
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
from playwright.sync_api import sync_playwright
from workflow.portal_session_runtime import session_manager
from workflow.portal_sessions import classify_probe

CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
HERE = "/home/ubuntu/job_hunt_linkedin"
PROFILE = os.environ.get("LI_LOGIN_PROFILE", "/home/ubuntu/tmp_chrome/li_login_profile")
EMAIL = os.environ.get("JOBHUNT_EMAIL", "")
PASSWORD = os.environ.get("GOOGLE_PASSWORD", "")
CONTINUE_LABELS = ["Continue", "Weiter", "Fortfahren", "Weiter zu LinkedIn", "Zulassen", "Allow",
                   "Ja, erlauben", "Bestätigen", "Confirm"]

def has_li_at(ctx):
    return any(c.get("name") == "li_at" for c in ctx.cookies())
def is_li_live(u):
    """True only when the HOST is linkedin.com and we're not on a login-ish path."""
    try:
        parts = up.urlsplit(u)
        host = (parts.hostname or "").lower()
        return (host.endswith("linkedin.com") and "login" not in parts.path
                and "authwall" not in u and "checkpoint" not in u and "challenge" not in u)
    except Exception:
        return False

MANAGER = session_manager()
LEASE = MANAGER.acquire_renewal(
    "linkedin", f"li-relogin:{socket.gethostname()}:{os.getpid()}", ttl_seconds=1800
)
_RELEASED = False


def release_renewal():
    global _RELEASED
    if _RELEASED:
        return
    try:
        MANAGER.release_renewal("linkedin", LEASE.token)
    except Exception:
        pass
    _RELEASED = True


atexit.register(release_renewal)


def publish_state(ctx, playwright):
    """Stage, independently probe, and atomically promote a LinkedIn candidate."""
    state = ctx.storage_state()
    candidate = MANAGER.stage_candidate("linkedin", state, LEASE.token)
    probe_browser = probe_context = None
    outcome, detail = "unknown", "probe-inconclusive"
    try:
        probe_browser = playwright.chromium.launch(
            executable_path=CLOAK,
            headless=True,
            args=["--no-first-run", "--no-default-browser-check"],
        )
        probe_context = probe_browser.new_context(storage_state=state)
        probe_page = probe_context.new_page()
        probe_page.goto(
            "https://www.linkedin.com/feed/",
            wait_until="domcontentloaded",
            timeout=45000,
        )
        try:
            body = probe_page.inner_text("body")[:5000]
        except Exception:
            body = ""
        outcome = classify_probe(probe_page.url, probe_page.title(), body)
        if outcome == "valid" and "/feed" not in probe_page.url.lower():
            outcome = "unknown"
        detail = {
            "valid": "authenticated-endpoint-accepted",
            "expired": "authentication-required",
            "challenged": "challenge-detected",
            "unknown": "probe-inconclusive",
        }[outcome]
    except Exception:
        outcome, detail = "unknown", "probe-network-error"
    finally:
        if probe_context is not None:
            probe_context.close()
        if probe_browser is not None:
            probe_browser.close()
    MANAGER.record_probe(
        "linkedin", candidate.id, outcome, LEASE.token, detail
    )
    if outcome != "valid":
        raise RuntimeError(f"candidate probe did not validate: {outcome}")
    promoted = MANAGER.promote("linkedin", candidate.id, LEASE.token)
    print(f"SESSION REVISION {promoted.revision} PROMOTED", flush=True)

def click_continue(w):
    for label in CONTINUE_LABELS:
        try:
            b = w.locator(f"button:has-text('{label}')").first
            if b.count() and b.is_visible():
                b.click(force=True, timeout=6000)
                print(f"clicked continue: {label} on {w.url[:80]}", flush=True)
                return True
        except Exception:
            pass
    try:
        card = w.locator("div[data-identifier], li[data-email], div[data-account-id]").first
        if card.count() and card.is_visible():
            card.click(timeout=6000)
            print("clicked account card", flush=True)
            return True
    except Exception:
        pass
    return False

def click_gsi_frame(page, ctx):
    for fr in page.frames:
        if "accounts.google.com" in (fr.url or ""):
            try:
                btn = fr.locator("div[role=button], button")
                for i in range(btn.count()):
                    if btn.nth(i).is_visible():
                        with ctx.expect_page(timeout=20000) as popup_info:
                            btn.nth(i).click(force=True, timeout=8000)
                        return popup_info.value
            except Exception as e:
                print("gsi-frame click err:", str(e)[:80], flush=True)
                return None
    return None

def extract_oauth_url(text):
    """Find the oauth2/auth redirect URL inside a response body (HTML or JS)."""
    m = re.search(r"https://accounts\.google\.com/o/oauth2/auth[^'\"\s<>\\]+", text)
    if m:
        return m.group(0)
    m = re.search(r"location(?:\.replace|\.href|\.assign)?\(['\"]([^'\"]*oauth2/auth[^'\"]*)['\"]", text)
    if m:
        return m.group(1)
    return None

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=PROFILE, executable_path=CLOAK, headless=True,
        args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
              "--window-size=1400,900"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    print("goto linkedin login", flush=True)
    page.goto("https://www.linkedin.com/login", wait_until="commit", timeout=60000)
    page.wait_for_timeout(6000)

    auth_page = click_gsi_frame(page, ctx)
    if auth_page:
        print("POPUP:", auth_page.url[:160], flush=True)
    else:
        print("no popup on initial click", flush=True)

    captured = {"req": None, "resp": None}
    for w in list(ctx.pages):
        if "gsi" in (w.url or ""):
            def on_req(r):
                if "gsi" in r.url and r.method == "POST":
                    captured["req"] = {"url": r.url, "body": r.post_data}
                    print("CAPTURED POST REQ", flush=True)
            def on_resp(r):
                if "gsi" in r.url and r.request.method == "POST":
                    try:
                        captured["resp"] = {"status": r.status, "body": r.text()[:3000]}
                        print(f"CAPTURED POST RESP {r.status}", flush=True)
                    except Exception as e:
                        print("resp read err:", str(e)[:60], flush=True)
            w.on("request", on_req)
            w.on("response", on_resp)
            # also watch console errors
            w.on("console", lambda m: print("CONSOLE:", m.type, m.text[:140]) if m.type in ("error", "warning") else None)

    NATIVE_TRIED = [False]
    APP_PUSH_RESENT = [False]

    for step in range(90):
        if has_li_at(ctx) and not any(("checkpoint" in (w.url or "") or "challenge" in (w.url or "")) for w in ctx.pages):
            print("LI_AT CAPTURED", flush=True)
            publish_state(ctx, p)
            ctx.close(); break
        for w in ctx.pages:
            u = w.url or ""
            try:
                el = w.locator("input#identifierId").first
                if el.count() and el.is_visible() and not el.input_value():
                    el.fill(EMAIL); w.keyboard.press("Enter")
                    print("email filled", flush=True); continue
                pw = w.locator("input[type=password]").first
                if "accounts.google.com" in u and pw.count() and pw.is_visible() and not pw.input_value():
                    pw.fill(PASSWORD); w.keyboard.press("Enter")
                    print("password filled", flush=True); continue
            except Exception:
                pass
            # --- LINKEDIN phone-OTP checkpoint: fill code from otp.txt ---
            li_otp_page = False
            try:
                _p = up.urlsplit(u).path.lower()
                li_otp_page = ("linkedin.com" in u) and ("checkpoint" in _p or "challenge" in _p)
            except Exception:
                pass
            if li_otp_page:
                try:
                    w.screenshot(path="/tmp/checkpoint_live.png")
                except Exception as e:
                    print("shot err:", str(e)[:60], flush=True)
                # --- detect LinkedIn APP-PUSH approval screen (not an OTP field) ---
                try:
                    body_txt = w.inner_text("body")[:300].replace("\n", " | ")
                    if ("linkedin app" in body_txt.lower() or "in der linkedin app" in body_txt.lower()
                            or "erneut senden" in body_txt.lower() or "send again" in body_txt.lower()):
                        # sms_route.txt trigger: switch from app-push to SMS challenge
                        if os.path.exists(os.path.join(HERE, "sms_route.txt")):
                            try:
                                link = w.get_by_role("link", name=re.compile(r"keinen Zugriff|no access|not have access|can'?t access", re.I)).first
                                if link.count() and link.is_visible():
                                    link.click(timeout=5000)
                                    print("ROUTED to SMS challenge (clicked no-access link)", flush=True)
                                    try:
                                        os.remove(os.path.join(HERE, "sms_route.txt"))
                                    except Exception:
                                        pass
                                else:
                                    print("no-access link not found, body:", body_txt[:200], flush=True)
                            except Exception as e:
                                print("route err:", str(e)[:100], flush=True)
                            continue
                        if not APP_PUSH_RESENT[0]:
                            # resend the push once, then wait for user to approve in app
                            try:
                                b = w.get_by_role("button", name=re.compile(r"erneut senden|send again|resend", re.I)).first
                                if b.count() and b.is_visible():
                                    b.click(timeout=5000)
                                    print("APP-PUSH checkpoint — clicked 'Send again', user must approve in LinkedIn app", flush=True)
                            except Exception:
                                print("APP-PUSH checkpoint — user must approve in LinkedIn app", flush=True)
                            APP_PUSH_RESENT[0] = True
                        else:
                            print("APP-PUSH checkpoint — waiting for user approval in LinkedIn app", flush=True)
                        continue
                except Exception:
                    pass
                otp_file = os.path.join(HERE, "otp.txt")
                if os.path.exists(otp_file):
                    code = open(otp_file).read().strip()
                    if re.fullmatch(r"\d{4,8}", code):
                        filled = False
                        try:
                            boxes = w.locator("input[type=text], input[type=tel], input[inputmode=numeric], input[name*=pin i], input[name*=otp i], input[name*=verif i]")
                            n = boxes.count()
                            if n == 1:
                                boxes.first.fill(code)
                                filled = True
                            elif n >= 4:
                                for i, ch in enumerate(code):
                                    boxes.nth(i).fill(ch)
                                filled = True
                            else:
                                inp = w.locator("input").first
                                if inp.count() and inp.is_visible():
                                    inp.fill(code)
                                    filled = True
                        except Exception as e:
                            print("fill err:", str(e)[:80], flush=True)
                        if filled:
                            try:
                                inv = w.locator("input")
                                val = inv.first.input_value() if inv.count() else ""
                                clean = re.sub(r"\D", "", val)
                                if clean != code:
                                    print(f"typed mismatch: got '{val}' want '{code}', skipping click", flush=True)
                                    filled = False
                            except Exception as e:
                                print("verify err:", str(e)[:60], flush=True)
                        if filled:
                            clicked = False
                            for label in ["Senden", "Send", "Submit", "Verify", "Weiter", "Bestätigen", "Continue"]:
                                try:
                                    b = w.get_by_role("button", name=label, exact=True).first
                                    if b.count() and b.is_visible():
                                        b.click(timeout=5000)
                                        print(f"OTP SUBMITTED ({label})", flush=True)
                                        clicked = True
                                        break
                                except Exception:
                                    pass
                            if not clicked:
                                try:
                                    btn_inv = w.evaluate("""() => [...document.querySelectorAll('button')].map(b => (b.innerText||'').trim()).filter(t=>t)""")
                                    print("BTNS:", btn_inv, flush=True)
                                except Exception:
                                    pass
                            os.remove(otp_file)
                        else:
                            try:
                                inv = w.evaluate("""() => [...document.querySelectorAll('input')].map(i => ({t:i.type, n:i.name, id:i.id, im:i.inputmode, v:i.offsetParent!==null, ph:i.placeholder||''}))""")
                                body = w.inner_text("body")[:400].replace("\n", " | ")
                                print("INV:", inv, flush=True)
                                print("BODY:", body, flush=True)
                            except Exception as e:
                                print("diag err:", str(e)[:80], flush=True)
                            try:
                                b = w.locator("button:has-text('Send code'), button:has-text('Text me'), button:has-text('Resend')").first
                                if b.count() and b.is_visible():
                                    b.click(timeout=5000)
                                    print("clicked SEND CODE button", flush=True)
                            except Exception:
                                pass
                    else:
                        print("OTP file invalid, waiting", flush=True)
                else:
                    print("WAITING FOR OTP (phone **5868) — expecting otp.txt", flush=True)
                continue
            # --- NATIVE LinkedIn login fallback (bypasses Google OAuth entirely) ---
            if "linkedin.com" in u and ("challenge_global" in u or step >= 36) and not NATIVE_TRIED[0]:
                try:
                    _lp = up.urlsplit(u).path.lower()
                    on_login = ("/login" in _lp or "flagship-web/login" in u)
                except Exception:
                    on_login = False
                if on_login:
                    NATIVE_TRIED[0] = True
                    try:
                        un = w.locator("input#username").first
                        if un.count() and un.is_visible():
                            un.fill(EMAIL)
                        else:
                            un = w.locator("input[autocomplete=username]").first
                            if un.count() and un.is_visible():
                                un.fill(EMAIL)
                        pw = w.locator("input#password").first
                        if pw.count() and pw.is_visible():
                            pw.fill(PASSWORD)
                        btn = w.get_by_role("button", name=re.compile(r"einloggen|sign in|log in", re.I)).first
                        if btn.count() and btn.is_visible():
                            btn.click(timeout=6000)
                            print("NATIVE LOGIN SUBMITTED", flush=True)
                    except Exception as e:
                        print("native login err:", str(e)[:80], flush=True)
                    continue
            # --- google challenge / device-push: extract number, DO NOT click ---
            if "accounts.google.com" in u and ("challenge" in u or "selectchallenge" in u or "/dp" in u or "identity" in u):
                try:
                    txt = w.inner_text("body")
                    nums = re.findall(r"\b\d{2}\b", txt)
                    print("CHALLENGE NUMBERS:", nums[:6], flush=True)
                    w.screenshot(path="/tmp/challenge_live.png")
                    print("CHALLENGE SHOT SAVED", flush=True)
                except Exception as e:
                    print("challenge err:", str(e)[:80], flush=True)
                continue
            if "gsi" in u:
                # click the account row containing our email (trusted event first)
                clicked = False
                for sel in ["li:has-text('@gmail.com')", "[data-identifier]",
                            "[data-email]", "div[role=button]:has-text('@gmail.com')",
                            "div[role=link]:has-text('@gmail.com')", "div:has-text('@gmail.com')"]:
                    try:
                        el = w.locator(sel).last
                        if el.count():
                            el.click(force=True, timeout=6000)
                            print(f"clicked account row: {sel}", flush=True)
                            clicked = True
                            break
                    except Exception as e:
                        print(f"row click err ({sel}): {str(e)[:60]}", flush=True)
                if not clicked:
                    try:
                        clicked = w.evaluate("""() => {
                            const all = [...document.querySelectorAll('li, div, span, [role=button], [role=link], [data-identifier], [data-email]')];
                            const hits = all.filter(e => (e.innerText||'').includes('@gmail.com'));
                            if (!hits.length) return false;
                            hits.sort((a,b) => (a.innerText||'').length - (b.innerText||'').length);
                            hits[0].click();
                            return true;
                        }""")
                        if clicked:
                            print("clicked account row (js fallback)", flush=True)
                    except Exception as e:
                        print("js click err:", str(e)[:60], flush=True)
                if not clicked:
                    if step % 6 == 0:
                        try:
                            info = w.evaluate("""() => ({
                                liCount: document.querySelectorAll('li').length,
                                bodyLen: document.body ? document.body.innerText.length : 0,
                                txt: (document.body.innerText||'').slice(0,150).replace(/\\n/g,' | ')
                            })""")
                            print("DIAG:", info, flush=True)
                        except Exception as e:
                            print("diag err:", str(e)[:60], flush=True)
                    if step % 6 == 3:
                        try:
                            for _w in list(ctx.pages):
                                if _w != page and "gsi/select" in (_w.url or ""):
                                    _w.close()
                            auth_page = click_gsi_frame(page, ctx)
                            if auth_page:
                                print("GSI-IFRAME FALLBACK POPUP:", auth_page.url[:120], flush=True)
                        except Exception as e:
                            print("gsi fallback err:", str(e)[:80], flush=True)
                continue
            if "accounts.google.com" in u:
                try:
                    body = w.inner_text("body")[:1500]
                except Exception:
                    body = ""
                if "LinkedIn" in body or "linkedin" in u.lower():
                    click_continue(w)
                    continue
                print("google page (no consent text):", u[:90], flush=True)
                continue
            if "linkedin.com" in u and ("consent" in u or "authorization" in u or "oauth" in u):
                for label in CONTINUE_LABELS:
                    try:
                        b = w.locator(f"button:has-text('{label}')").first
                        if b.count() and b.is_visible():
                            b.click(timeout=5000)
                            print("clicked li consent:", label, flush=True)
                            break
                    except Exception:
                        pass
                continue
        for w in ctx.pages:
            u = w.url
            if is_li_live(u) and has_li_at(ctx):
                print("LINKEDIN LIVE:", u[:110], flush=True)
                publish_state(ctx, p)
                ctx.close(); raise SystemExit(0)
            if is_li_live(u) and not has_li_at(ctx):
                print("LI page live but no li_at yet:", u[:100], flush=True)
        if not any("accounts.google.com" in (w.url or "") and "gsi/button" not in (w.url or "") for w in ctx.pages):
            for w in ctx.pages:
                try:
                    _lp = up.urlsplit(w.url or "").path.lower()
                except Exception:
                    _lp = ""
                if "linkedin.com" in (w.url or "") and "/login" in _lp:
                    if step % 12 == 0 and step < 60:
                        auth_page = click_gsi_frame(w, ctx)
                        if auth_page:
                            print("POPUP (retry):", auth_page.url[:100], flush=True)
                    break
        page.wait_for_timeout(5000)
        if step % 6 == 0:
            print(f"[{step*5}s] pages: {[w.url[:70] for w in ctx.pages]}", flush=True)
    ctx.close()
print("DONE", flush=True)
