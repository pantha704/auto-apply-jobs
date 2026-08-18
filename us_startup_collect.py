#!/usr/bin/env python3
"""US/startup job collectors: YC Work-at-a-Startup, WeWorkRemotely, Himalayas, Wellfound SF remote."""
from playwright.sync_api import sync_playwright
import json, os
import time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
OUT = "/tmp/us_startup_jobs.json"

SITES = [
    {
        "name": "yc",
        "url": "https://www.workatastartup.com/companies?query=software&sortBy=created_desc",
        "extract": """() => {
          const out = [];
          document.querySelectorAll('a[href*="/companies/"]').forEach(a => {
            const t = (a.innerText || a.textContent || '').trim().replace(/\\s+/g,' ').slice(0,100);
            if (t.length < 4 || /load more/i.test(t)) return;
            out.push({title: t, link: a.href});
          });
          return out.slice(0, 250);
        }""",
        "scrolls": 8,
    },
    {
        "name": "weworkremotely",
        "url": "https://weworkremotely.com/categories/remote-full-stack-programming-jobs",
        "extract": """() => {
          const out = [];
          document.querySelectorAll('li:not([class]) a, .jobs-container li a, section.jobs a').forEach(a => {
            const t = (a.innerText || a.textContent || '').trim().replace(/\\s+/g,' ').slice(0,100);
            const href = a.href || '';
            if (t.length < 4 || !href.includes('weworkremotely.com/remote-jobs')) return;
            out.push({title: t, link: href});
          });
          return out.slice(0, 200);
        }""",
        "scrolls": 4,
    },
    {
        "name": "himalayas",
        "url": "https://himalayas.app/jobs/software-engineer",
        "extract": """() => {
          const out = [];
          document.querySelectorAll('a[href*="/companies/"][href*="/jobs/"]').forEach(a => {
            const href = a.href || '';
            if (!/himalayas\\.app\\/companies\\/.+\\/jobs\\/\\d+/.test(href)) return;
            const t = (a.innerText || a.textContent || '').trim().replace(/\\s+/g,' ').slice(0,100);
            if (t.length < 4) return;
            out.push({title: t, link: href});
          });
          return out.slice(0, 200);
        }""",
        "scrolls": 6,
    },
    {
        "name": "wellfound_us",
        "url": "https://wellfound.com/role/r/software-engineer?location=San+Francisco",
        "extract": """() => {
          const out = [];
          document.querySelectorAll('a[href*="/role/"]').forEach(a => {
            const href = a.href || '';
            if (/page=\\\\d+/.test(href)) return;
            const t = (a.innerText || a.textContent || '').trim().replace(/\\s+/g,' ').slice(0,100);
            if (t.length < 4 || /^\\\\d+$/.test(t)) return;
            out.push({title: t, link: href});
          });
          return out.slice(0, 200);
        }""",
        "scrolls": 10,
    },
]

results = []
with sync_playwright() as p:
    for site in SITES:
        try:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir="/tmp/cloak_profile_us", executable_path=CLOAK, headless=True,
                args=["--no-first-run", "--disable-blink-features=AutomationControlled", "--window-size=1280,900"])
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(site["url"], wait_until="domcontentloaded", timeout=50000)
            page.wait_for_timeout(6000)
            for _ in range(site["scrolls"]):
                page.mouse.wheel(0, 3000)
                page.wait_for_timeout(900)
            data = page.evaluate(site["extract"])
            seen, uniq = set(), []
            for j in data:
                if j["link"] not in seen:
                    seen.add(j["link"]); uniq.append(j)
            results.append({"site": site["name"], "count": len(uniq), "jobs": uniq})
            print(f"[{site['name']}] {len(uniq)}", flush=True)
            ctx.close()
        except Exception as e:
            results.append({"site": site["name"], "error": str(e)[:150]})
            print(f"[{site['name']}] ERR {str(e)[:100]}", flush=True)
        time.sleep(2)

json.dump(results, open(OUT, "w"), indent=1)
print(f"saved {OUT}")
