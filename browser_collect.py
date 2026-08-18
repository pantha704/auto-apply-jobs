#!/usr/bin/env python3
"""Browser-based job collector for JS-heavy sites: Web3.career, CryptoJobsList, Wellfound, RemoteOK."""
from playwright.sync_api import sync_playwright
import json, os
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome", re, time

PROFILE = "/home/ubuntu/.config/google-chrome/Profile 4"
OUT = "/tmp/site_jobs.json"
STEALTH_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

SITES = [
    {
        "name": "cryptojobslist",
        "url": "https://cryptojobslist.com/jobs",
        "extract": """() => {
          const out = [];
          document.querySelectorAll('a[href*="/jobs/"], a[href*="/job/"]').forEach(a => {
            const t = (a.innerText || a.textContent || '').trim().replace(/\\s+/g,' ').slice(0,120);
            if (t.length < 5) return;
            out.push({title: t, link: a.href});
          });
          return out.slice(0, 200);
        }""",
    },
    {
        "name": "wellfound",
        "url": "https://wellfound.com/role/r/remote-software-engineer",
        "extract": """() => {
          const out = [];
          document.querySelectorAll('a[href*="/role/"], a[href*="/companies/"]').forEach(a => {
            const t = (a.innerText || a.textContent || '').trim().replace(/\\s+/g,' ').slice(0,120);
            if (t.length < 5) return;
            out.push({title: t, link: a.href});
          });
          return out.slice(0, 200);
        }""",
    },
]

def main():
    results = []
    with sync_playwright() as p:
        ctx = None
        def ensure_ctx():
            nonlocal ctx
            if ctx is None or not ctx.pages:
                try:
                    if ctx: ctx.close()
                except Exception: pass
                ctx = p.chromium.launch_persistent_context(
                    user_data_dir=PROFILE, executable_path=CLOAK, headless=False,
                    args=["--no-first-run", "--no-default-browser-check", "--disable-sync",
                          "--disable-blink-features=AutomationControlled", "--window-size=1280,900"])
                ctx.add_init_script(STEALTH_JS)
            return ctx.pages[0] if ctx.pages else ctx.new_page()

        for site in SITES:
            try:
                page = ensure_ctx()
                page.goto(site["url"], wait_until="domcontentloaded", timeout=50000)
                page.wait_for_timeout(6000)
                for _ in range(5):
                    page.mouse.wheel(0, 2500)
                    page.wait_for_timeout(800)
                data = page.evaluate(site["extract"])
                seen, uniq = set(), []
                for j in data:
                    if j["link"] not in seen:
                        seen.add(j["link"]); uniq.append(j)
                results.append({"site": site["name"], "count": len(uniq), "jobs": uniq[:120]})
                print(f"[{site['name']}] {len(uniq)} extracted", flush=True)
            except Exception as e:
                results.append({"site": site["name"], "error": str(e)[:150]})
                print(f"[{site['name']}] ERROR {str(e)[:120]}", flush=True)
                try:
                    if ctx: ctx.close()
                except Exception: pass
                ctx = None
            try:
                page.wait_for_timeout(2000)
            except Exception:
                pass
        if ctx:
            try: ctx.close()
            except Exception: pass

    json.dump(results, open(OUT, "w"), indent=1)
print(f"saved {OUT}")


if __name__ == '__main__':
    main()
