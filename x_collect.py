#!/usr/bin/env python3
"""X (Twitter) job-tweet collector via CloakBrowser. Multiple keyword searches."""
from playwright.sync_api import sync_playwright
import json, os
import re,time
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
OUT = "/tmp/x_jobs.json"
SEARCHES = [
    '"we\'re hiring" (solana OR web3 OR rust) lang:en',
    '"we are hiring" (full stack OR typescript OR react) lang:en',
    '"hiring" (solana OR web3 developer) lang:en',
    '"hiring" (remote developer OR software engineer) (india OR kolkata) lang:en',
    '"open to work" OR "hiring" (next.js OR node.js OR python developer) lang:en',
]

def extract(page):
    return page.evaluate("""() => {
      const out = [];
      document.querySelectorAll('article[data-testid="tweet"]').forEach(t => {
        const text = (t.innerText || '').trim().replace(/\\s+/g,' ').slice(0, 280);
        if (text.length < 20) return;
        const links = [...t.querySelectorAll('a')].map(a => a.href).filter(h => h && !h.includes('/pantha704/') && !h.includes('x.com/search'));
        const hl = [...t.querySelectorAll('a')].filter(a => a.href && /linkedin.com\\/jobs|wellfound|naukri|indeed|lever.co|greenhouse|breezy|workable|apply/.test(a.href)).map(a => a.href);
        out.push({text, links: links.slice(0,5), apply_links: hl.slice(0,3)});
      });
      return out;
    }""")

results = []
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir="/tmp/cloak_profile", executable_path=CLOAK, headless=False,
        args=["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
              "--window-size=1400,900"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    for q in SEARCHES:
        url = "https://x.com/search?q=" + q.replace(" ", "%20").replace('"', "%22") + "&f=live"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(5000)
            for _ in range(6):
                page.mouse.wheel(0, 3500)
                page.wait_for_timeout(1000)
            tweets = extract(page)
            for t in tweets:
                t["query"] = q
            results.extend(tweets)
            print(f"[{q[:45]}] {len(tweets)} tweets", flush=True)
        except Exception as e:
            print(f"[{q[:45]}] ERR {str(e)[:80]}", flush=True)
        time.sleep(2)
    ctx.close()

# dedupe by text
seen, uniq = set(), []
for t in results:
    key = t["text"][:120]
    if key not in seen:
        seen.add(key); uniq.append(t)
json.dump({"count": len(uniq), "jobs": uniq}, open(OUT, "w"), indent=1)
print(f"TOTAL UNIQUE TWEETS: {len(uniq)} -> {OUT}")
