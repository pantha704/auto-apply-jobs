#!/usr/bin/env python3
"""portal_guard.py — autonomous session renewal for the WHOLE job-hunt farm.

Probes every portal's saved session with a real authed request. If a session is
dead (server-side revocation — the LinkedIn killer), renews it automatically where
a scripted path exists (wellfound, internshala, linkedin via li_session_guard.sh),
else raises an alert for the one-time-human portals (yc magic-link, himalayas, naukri).

Rules (learned the hard way):
- Live probe > cookie expiry math (revocation, pitfall 2026-08-17).
- Never login while scraping (pitfall 28 — LinkedIn risk engine).
- One action per state; passive waits; never spam clicks (LO's rate-limit rule).
- flock single-flight; cooldown per portal; bounded renewals.
Silent when everything is healthy (watchdog pattern).
"""
import json, os, re, subprocess, sys, time, glob, urllib.parse as up
from pathlib import Path

HERE = "/home/ubuntu/job_hunt_linkedin"
LOG = Path(HERE) / "logs" / "portal_guard.log"
LOCK = Path("/tmp/portal_guard.lock")
STATE = Path("/tmp/portal_guard_state.json")  # renewal cooldown + alert dedup
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0 Safari/537.36"
HIMA_USER = "832960"

PORTALS = {
    "wellfound": {
        "jar": "portal_wellfound.json", "probe": "https://wellfound.com/settings",
        "ok": lambda u: "wellfound.com/settings" in u and "/login" not in u,
    },
    "internshala": {
        "jar": "portal_internshala.json", "probe": "https://internshala.com/student/dashboard",
        "ok": lambda u: "internshala.com" in u and "/login" not in u and "student" in u,
    },
    "yc": {
        "jar": "portal_yc.json", "probe": "https://account.ycombinator.com/",
        "ok": lambda u: "account.ycombinator.com" in u and "/auth" not in u,
    },
    "himalayas": {
        "jar": "portal_himalayas.json", "probe": None,  # CF-walled: curl can NEVER pass (clearance is browser-fingerprint-bound) — probe via real browser
        "ok": lambda u: True,
        "playwright_probe": True,
    },
    "naukri": {
        "jar": "portal_naukri.json", "probe": "https://www.naukri.com/mnjuser/homepage",
        "ok": lambda u: "naukri.com/mnjuser" in u and "/login" not in u,
        "skip": True,  # Akamai IP-block from VPS — can't even probe reliably
    },
}

def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")

def load_state():
    if STATE.exists():
        try: return json.loads(STATE.read_text())
        except Exception: pass
    return {"cooldown": {}, "alerts": {}}

def save_state(s):
    STATE.write_text(json.dumps(s))

def jar_from_state(json_path, jar_path, domain_hint):
    """portal json -> Netscape cookie jar (filtered to the portal's domain + cf cookies)."""
    try:
        d = json.load(open(json_path))
    except Exception:
        return False
    lines = ["# Netscape HTTP Cookie File"]
    hint = domain_hint
    for c in d.get("cookies", []):
        dom = (c.get("domain") or "").lstrip(".")
        name = c.get("name", "")
        if hint not in dom and name != "cf_clearance":
            continue
        secure = "TRUE" if c.get("secure") else "FALSE"
        exp = c.get("expires", -1)
        exp = int(exp) if isinstance(exp, (int, float)) and exp > 0 else 0
        lines.append(f"{dom}\tTRUE\t{c.get('path','/')}\t{secure}\t{exp}\t{name}\t{c.get('value','')}")
    open(jar_path, "w").write("\n".join(lines))
    return True

def probe_himalayas_browser():
    """CF-walled portal: load the saved-cookie profile in a real browser and read login state.
    avatar/notification markers = logged in; 'Log in' button = dead."""
    from playwright.sync_api import sync_playwright
    CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
    DST = "/tmp/hima_probe_profile"
    subprocess.run(["rm", "-rf", DST])
    subprocess.run(["cp", "-a", os.path.join(HERE, "profiles", "hima_cap"), DST])
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=DST, executable_path=CLOAK, headless=True,
                args=["--no-first-run", "--no-default-browser-check",
                      "--disable-blink-features=AutomationControlled", "--window-size=1280,720"])
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://himalayas.app/", wait_until="commit", timeout=45000)
            # CF interstitial may show briefly; give it time to auto-clear with valid clearance
            body = ""
            for _ in range(5):
                page.wait_for_timeout(3000)
                try:
                    body = page.inner_text("body")[:2000]
                except Exception:
                    body = ""
                if "Just a moment" not in body and "security verification" not in body.lower():
                    break
            logged_in = ("Log in" not in body) and ("Sign up" not in body) and (
                "notifications" in body.lower() or "talent" in body.lower() or "streak" in body.lower())
            ctx.close()
            return "ok" if logged_in else "dead"
    except Exception as e:
        return f"error:{str(e)[:60]}"

def probe(portal, name):
    cfg = PORTALS[name]
    if cfg.get("skip"):
        return "skip"
    if cfg.get("playwright_probe"):
        return probe_himalayas_browser()
    jar = f"/tmp/pg_{name}.jar"
    if not jar_from_state(os.path.join(HERE, cfg["jar"]), jar, name):
        return "missing-state"
    try:
        cmd = ["curl", "-s", "-L", "-o", "/dev/null", "--max-time", "25",
               "-A", UA, "-b", jar, "-w", "%{http_code} %{url_effective}", cfg["probe"]]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip()
        code, url = (out.split(" ", 1) + [""])[:2]
        if cfg.get("check_status"):
            return "ok" if code.startswith("2") and not code.startswith("40") else (
                "ok" if code == "200" else "dead")
        return "ok" if cfg["ok"](url) else "dead"
    except Exception as e:
        return f"error:{str(e)[:60]}"

def scraping_running():
    r = subprocess.run(["pgrep", "-f", "scrape_jobs|realtime_pull|site_collect"], 
                       capture_output=True, text=True).stdout.split()
    return [p for p in r if p and p != str(os.getpid())]

def renew_wellfound():
    """Copy warm Google session (li_login_profile) -> ride SSO -> export portal_wellfound.json."""
    from playwright.sync_api import sync_playwright
    CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
    DST = "/tmp/wf_renew_profile"
    subprocess.run(["rm", "-rf", DST])
    subprocess.run(["cp", "-a", "/home/ubuntu/tmp_chrome/li_login_profile", DST])
    out_path = os.path.join(HERE, "portal_wellfound.json")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=DST, executable_path=CLOAK, headless=True,
            args=["--no-first-run", "--no-default-browser-check",
                  "--disable-blink-features=AutomationControlled", "--window-size=1400,900"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://wellfound.com/login", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)
        clicked = False
        for label in ["Continue with Google", "Log in with Google", "Sign in with Google"]:
            b = page.locator(f"button:has-text('{label}'), a:has-text('{label}')").first
            if b.count():
                b.click(force=True, timeout=8000)
                clicked = True
                break
        if not clicked:
            a = page.locator("a[href*='google']").first
            if a.count():
                a.click(force=True, timeout=8000)
                clicked = True
        # ride the google flow: chooser row + consent
        deadline = time.time() + 180
        while time.time() < deadline:
            for w in ctx.pages:
                u = w.url or ""
                if "accounts.google.com" in u and "gsi/button" not in u:
                    if "chooser" in u or "gsi" in u or "signin" in u:
                        try:
                            w.evaluate("""() => {
                              const all = [...document.querySelectorAll('li, div, span, [role=button], [role=link], [data-identifier], [data-email]')];
                              const hits = all.filter(e => (e.innerText||'').includes('@gmail.com'));
                              if (!hits.length) return false;
                              hits.sort((a,b) => (a.innerText||'').length - (b.innerText||'').length);
                              hits[0].click(); return true;
                            }""")
                            time.sleep(4)
                        except Exception:
                            pass
                    if "consent" in u:
                        for label in ["Continue", "Allow", "Weiter", "Zulassen"]:
                            b = w.locator(f"button:has-text('{label}')").first
                            if b.count() and b.is_visible():
                                b.click(force=True, timeout=5000)
                                break
                    time.sleep(6)
            wf = [c for c in ctx.cookies() if "wellfound.com" in (c.get("domain") or "")]
            # real auth marker: the wellfound session cookie
            sess = [c for c in wf if c["name"] in ("_wellfound", "user_signed_in", "session")]
            if sess:
                json.dump({"cookies": ctx.cookies()}, open(out_path, "w"))
                ctx.close()
                return True
            time.sleep(6)
        ctx.close()
    return False

def renew_himalayas():
    """Run the linear SSO capture (hima_one_shot.py, patched headless).
    exit 0 = landed; exit 3 = unknown state, park for triage cron; else fail."""
    try:
        r = subprocess.run(["/home/ubuntu/jobhunt-venv/bin/python3",
                            os.path.join(HERE, "hima_one_shot.py")],
                           capture_output=True, text=True, timeout=480)
        log(f"  hima renew stdout: {r.stdout[-200:]}")
        if r.returncode == 0:
            return "ok"
        if r.returncode == 3:
            return "triaged"   # state_queue got it; task-state-triage cron will drive it
        return "fail"
    except subprocess.TimeoutExpired:
        return "timeout"

def renew_internshala():
    """Stop is worker -> capture_is4 (patient SSO) -> restart worker. Browser profile lock rule."""
    def sysctl(*args):
        return subprocess.run(["sudo", "-n", "systemctl"] + list(args), capture_output=True, text=True)
    sysctl("stop", "jobhunt-is@is-w1")
    time.sleep(3)
    try:
        r = subprocess.run(["/home/ubuntu/jobhunt-venv/bin/python3",
                            os.path.join(HERE, "capture_is4.py")],
                           capture_output=True, text=True, timeout=480)
        ok = "LANDED ON INTERNSHALA" in r.stdout
    except subprocess.TimeoutExpired:
        ok = False
    sysctl("start", "jobhunt-is@is-w1")
    return ok

def main():
    # single-flight
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o644)
    except Exception:
        return
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        log("previous run active, skipping")
        return
    st = load_state()
    now = time.time()
    alerts, done = [], []

    scraping = scraping_running()
    log(f"portal_guard start (scraping_running={bool(scraping)})")

    results = {}
    for name in PORTALS:
        results[name] = probe(name, name)
        log(f"  probe {name}: {results[name]}")

    for name, res in results.items():
        if res != "dead":
            st["alerts"].pop(name, None)
            continue
        # dead session — renew or alert
        if scraping:
            log(f"  {name} dead but scraping running — deferring renewal")
            continue
        cooldown_until = st["cooldown"].get(name, 0)
        if now < cooldown_until:
            log(f"  {name} dead, in cooldown till {time.strftime('%H:%M', time.localtime(cooldown_until))}")
            continue
        if name == "wellfound" and not os.path.exists("/home/ubuntu/tmp_chrome/li_login_profile"):
            # no warm google profile to copy — needs the google session first
            alerts.append(f"wellfound: session dead AND no Google-session profile to copy (needs one login)")
            continue
        if name in ("wellfound", "internshala", "himalayas"):
            log(f"  RENEWING {name}...")
            if name == "wellfound":
                ok = renew_wellfound()
            elif name == "internshala":
                ok = renew_internshala()
            else:
                st["cooldown"][name] = now + 6 * 3600
                r = renew_himalayas()
                if r == "ok":
                    done.append("himalayas: session renewed automatically")
                elif r == "triaged":
                    st["alerts"].pop(name, None)  # triage cron owns it now
                    log("  himalayas triaged to state_queue — triage cron continues it")
                else:
                    alerts.append(f"himalayas: renewal {r} — inspect logs/portal_guard.log")
                continue
            st["cooldown"][name] = now + 6 * 3600
            if ok:
                done.append(f"{name}: session renewed automatically")
            else:
                alerts.append(f"{name}: renewal FAILED (check logs/portal_guard.log)")
        else:
            # yc / himalayas / naukri — honest alert: needs one-time human or Gmail piece
            st["cooldown"][name] = now + 24 * 3600
            alerts.append(f"{name}: session dead, no full-auto renew path — needs the Gmail/one-time fix")

    save_state(st)
    for d in done:
        print(f"🔑 {d}")
    for a in alerts:
        print(f"⚠️ {a}")
    if not done and not alerts:
        log("all portals healthy — silent")

if __name__ == "__main__":
    main()