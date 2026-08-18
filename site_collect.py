#!/usr/bin/env python3
"""Naukri + Wellfound + Internshala collectors via CloakBrowser (guest)."""
from playwright.sync_api import sync_playwright
import json, os
import re,time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
OUT = "/tmp/site_collect.json"

WELLFOUND_EXTRACT = """() => {
  const out = [];
  // job cards link to /jobs/<id>-<slug>; /role/r/ and /role/l/ are SEARCH pages, not jobs
  document.querySelectorAll('a[href*="/jobs/"]').forEach(a => {
    const href = a.href || '';
    if (/page=\\d+/.test(href)) return;
    const t = (a.innerText || a.textContent || '').trim().replace(/\\s+/g,' ').slice(0,100);
    if (t.length < 4 || /^\\d+$/.test(t)) return;
    out.push({title: t, link: href});
  });
  return out.slice(0, 150);
}"""

SITES = [
    {
        "name": "naukri",
        "url": "https://www.naukri.com/full-stack-developer-jobs?workExp=0",
        "extract": """() => {
          const out = [];
          document.querySelectorAll('a[href*="job-listings"]').forEach(a => {
            const t = (a.innerText || a.textContent || '').trim().replace(/\\s+/g,' ').slice(0,90);
            if (t.length < 4) return;
            let card = a.closest('article') || a.closest('li') || a.parentElement;
            const comp = card ? card.querySelector('.subTitle, [class*=company], [class*=comp]') : null;
            const loc = card ? card.querySelector('[class*=location]') : null;
            out.push({title: t,
                      company: comp ? comp.innerText.trim().slice(0,50) : '',
                      location: loc ? loc.innerText.trim().slice(0,40) : '',
                      link: a.href || ''});
          });
          return out.slice(0, 150);
        }""",
        "scrolls": 6,
    },
    {
        "name": "wellfound",
        "url": "https://wellfound.com/role/r/software-engineer?location=India",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 10,
    },
    {
        "name": "wellfound_fullstack",
        "url": "https://wellfound.com/role/r/full-stack-engineer?location=Remote",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 10,
    },
    {
        "name": "wellfound_backend",
        "url": "https://wellfound.com/role/r/backend-engineer?location=Remote",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 10,
    },
    {
        "name": "wellfound_frontend",
        "url": "https://wellfound.com/role/r/frontend-engineer?location=Remote",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 10,
    },
    {
        "name": "wellfound_react",
        "url": "https://wellfound.com/role/r/react-developer?location=India",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 10,
    },
    {
        "name": "wellfound_python",
        "url": "https://wellfound.com/role/r/python-developer?location=Remote",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 10,
    },
    {
        "name": "wellfound_data",
        "url": "https://wellfound.com/role/r/data-engineer?location=Remote",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 10,
    },
    {
        "name": "wellfound_uk_fs",
        "url": "https://wellfound.com/role/r/full-stack-engineer?location=United%20Kingdom",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 6,
    },
    {
        "name": "wellfound_ca_fs",
        "url": "https://wellfound.com/role/r/full-stack-engineer?location=Canada",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 6,
    },
    {
        "name": "wellfound_de_backend",
        "url": "https://wellfound.com/role/r/backend-engineer?location=Germany",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 6,
    },
    {
        "name": "wellfound_nl_frontend",
        "url": "https://wellfound.com/role/r/frontend-engineer?location=Netherlands",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 6,
    },
    {
        "name": "wellfound_ca_react",
        "url": "https://wellfound.com/role/r/react-developer?location=Canada",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 6,
    },
    {
        "name": "wellfound_uk_data",
        "url": "https://wellfound.com/role/r/data-engineer?location=United%20Kingdom",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 6,
    },
    {
        "name": "wellfound_in_python",
        "url": "https://wellfound.com/role/r/python-developer?location=India",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 6,
    },
    {
        "name": "wellfound_ca_python",
        "url": "https://wellfound.com/role/r/python-developer?location=Canada",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 6,
    },
    {
        "name": "wellfound_rust",
        "url": "https://wellfound.com/role/r/rust-developer?location=Remote",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 6,
    },
    {
        "name": "wellfound_devops",
        "url": "https://wellfound.com/role/r/devops-engineer?location=Remote",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 6,
    },
    {
        "name": "wellfound_ai",
        "url": "https://wellfound.com/role/r/ai-engineer?location=Remote",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 6,
    },
    {
        "name": "wellfound_ml",
        "url": "https://wellfound.com/role/r/machine-learning-engineer?location=Remote",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 6,
    },
    {
        "name": "wellfound_blockchain",
        "url": "https://wellfound.com/role/r/blockchain-developer?location=Remote",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 6,
    },
    {
        "name": "wellfound_mobile",
        "url": "https://wellfound.com/role/r/mobile-engineer?location=Remote",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 8,
    },
    {
        "name": "wellfound_ios",
        "url": "https://wellfound.com/role/r/ios-engineer?location=Remote",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 8,
    },
    {
        "name": "wellfound_android",
        "url": "https://wellfound.com/role/r/android-engineer?location=Remote",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 8,
    },
    {
        "name": "wellfound_security",
        "url": "https://wellfound.com/role/r/security-engineer?location=Remote",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 8,
    },
    {
        "name": "wellfound_game",
        "url": "https://wellfound.com/role/r/game-developer?location=Remote",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 8,
    },
    {
        "name": "wellfound_sre",
        "url": "https://wellfound.com/role/r/site-reliability-engineer?location=Remote",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 8,
    },
    {
        "name": "wellfound_product",
        "url": "https://wellfound.com/role/r/product-engineer?location=Remote",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 8,
    },
    {
        "name": "wellfound_us_fs",
        "url": "https://wellfound.com/role/r/full-stack-engineer?location=United%20States",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 8,
    },
    {
        "name": "wellfound_us_backend",
        "url": "https://wellfound.com/role/r/backend-engineer?location=United%20States",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 8,
    },
    {
        "name": "wellfound_us_frontend",
        "url": "https://wellfound.com/role/r/frontend-engineer?location=United%20States",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 8,
    },
    {
        "name": "wellfound_us_react",
        "url": "https://wellfound.com/role/r/react-developer?location=United%20States",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 8,
    },
    {
        "name": "wellfound_sg_fs",
        "url": "https://wellfound.com/role/r/full-stack-engineer?location=Singapore",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 8,
    },
    {
        "name": "wellfound_au_fs",
        "url": "https://wellfound.com/role/r/full-stack-engineer?location=Australia",
        "extract": WELLFOUND_EXTRACT,
        "scrolls": 8,
    },
    {
        "name": "yc",
        "url": "https://www.workatastartup.com/companies?query=software&sortBy=created_desc",
        "extract": """() => {
          const out = [];
          document.querySelectorAll('a[href*="/companies/"]').forEach(a => {
            const href = a.href || '';
            const m = href.match(/\\/companies\\/([a-z0-9-]+)\\/?$/i);
            if (!m) return;                      // only company detail pages
            if (m[1] === 'companies' || m[1].length < 2) return;
            const t = (a.innerText || a.textContent || '').trim().replace(/\\s+/g,' ').slice(0,110);
            if (t.length < 3) return;
            out.push({title: t, link: href});
          });
          return out.slice(0, 150);
        }""",
        "scrolls": 8,
    },
    {
        "name": "internshala",
        "url": "https://internshala.com/internships/software-development-internships",
        "extract": """() => {
          const out = [];
          document.querySelectorAll('.internship_meta, .individual_internship').forEach(card => {
            const a = card.querySelector('a[href*="/internship/detail/"]');
            if (!a) return;
            const t = (a.innerText || a.textContent || '').trim().replace(/\\s+/g,' ').slice(0,100);
            if (t.length < 4) return;
            const comp = card.querySelector('.company_name, .company-name');
            const loc = card.querySelector('.locations, .location');
            out.push({title: t, company: comp ? comp.innerText.trim().slice(0,50) : '',
                      location: loc ? loc.innerText.trim().slice(0,40) : '',
                      link: a.href || ''});
          });
          return out.slice(0, 150);
        }""",
        "scrolls": 6,
    },
]

results = []
with sync_playwright() as p:
    for site in SITES:
        try:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir="/tmp/cloak_profile_site", executable_path=CLOAK, headless=True,
                args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
                      "--window-size=1280,900"])
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
