#!/usr/bin/env python3
"""Wellfound FRESH harvester — recency-sorted, SEO role pages.

Iterates Wellfound SEO role landing pages (/role/r/<role>?location=...&page=N),
dismisses consent, and extracts every JobListingSearchResult node from
__NEXT_DATA__.props.pageProps.apolloState.data. Each node carries liveStartAt
(epoch posted timestamp) + id + slug → canonical /jobs/{id}-{slug} URL.
Keeps only recently-posted roles. SQLite-backed dedupe by job URL.

Output (matches inject_site.py):
  [{"site": "wellfound_fresh", "jobs": [{"title","link","posted_at","posted_iso"}]}]
"""
import json, os, sys, time, sqlite3, re

from playwright.sync_api import sync_playwright

HERE = "/home/ubuntu/job_hunt_linkedin"
os.environ.setdefault("TMPDIR", "/home/ubuntu/tmp_chrome")
CLOAK = "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome"
PORTAL = os.path.join(HERE, "portal_wellfound.json")
PROFILE = "/tmp/cloak_profile_wf_fresh_seo"
OUT = "/tmp/wellfound_fresh.json"
STATE_DB = os.path.join(HERE, ".wellfound_fresh_seen.db")

FRESH_DAYS = 7        # hard fresh window — Wellfound closes listings within days; keep tight
RELAX_DAYS = 30       # only relax if we find fewer than MIN_KEEP fresh
MIN_KEEP = 5
MAX_KEEP = 150
PAGES_PER_ROLE = 2   # pagination depth per role page

# role → location pairs (SEO landing pages accept ?page=N; location= narrows to India/Remote)
ROLE_PAGES = [
    ("software-engineer", ""),
    ("software-engineer", "location=Remote"),
    ("full-stack-engineer", "location=Remote"),
    ("application-developer", "location=India"),
    ("backend-engineer", "location=Remote"),
    ("frontend-engineer", "location=Remote"),
    ("python-developer", "location=Remote"),
    ("react-developer", "location=India"),
    ("software-engineer-intern", ""),
    ("backend-engineer", "location=India"),
]

STEALTH_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
CONSENT_SELS = [
    "[id*=consent] button",
    "button:has-text('Accept')",
    "button:has-text('I accept')",
    "button:has-text('Allow all')",
]


def extract_entries(nd):
    """Pull JobListingSearchResult nodes (with liveStartAt) from apolloState.data."""
    entries_live = []
    apollo = nd.get("props", {}).get("pageProps", {}).get("apolloState", {})
    data = apollo.get("data", {}) if isinstance(apollo, dict) else {}
    for k, v in data.items():
        if k.startswith("JobListingSearchResult:") and isinstance(v, dict):
            if "liveStartAt" in v and v.get("id"):
                entries_live.append(v)
    return entries_live


def real_hrefs(page):
    """hrefs actually rendered in the DOM — reliably 200-able job URLs."""
    hrefs = page.evaluate("""() => {
      const s = new Set();
      document.querySelectorAll('a[href*="/jobs/"]').forEach(a => {
        const h = (a.getAttribute('href') || '').split('?')[0];
        if (/^\\/jobs\\/\\d+-[a-z0-9-]+$/.test(h)) s.add(h.split('/').pop());
      });
      return Array.from(s);
    }""")
    return set(hrefs)


def dismiss_consent(page):
    for sel in CONSENT_SELS:
        try:
            b = page.query_selector(sel)
            if b and b.is_visible():
                b.click()
                page.wait_for_timeout(2000)
                return True
        except Exception:
            continue
    return False


def main():
    # SQLite dedupe so repeat runs only emit postings we've never injected
    conn = sqlite3.connect(STATE_DB)
    conn.execute("CREATE TABLE IF NOT EXISTS seen (url TEXT PRIMARY KEY, injected_at INT)")
    seen_ever = {r[0] for r in conn.execute("SELECT url FROM seen")}

    fresh = {}  # canonical url -> posting dict
    with sync_playwright() as p:
        try:
            cookies = json.load(open(PORTAL)).get("cookies", [])
        except Exception:
            cookies = []
        ctx = None
        for role, loc in ROLE_PAGES:
            for page_num in range(1, PAGES_PER_ROLE + 1):
                query = f"?page={page_num}"
                if loc:
                    query += "&" + loc
                url = f"https://wellfound.com/role/r/{role}{query}"
                try:
                    if ctx is None:
                        ctx = p.chromium.launch_persistent_context(
                            user_data_dir=PROFILE, executable_path=CLOAK, headless=True,
                            args=["--no-first-run", "--no-default-browser-check",
                                  "--disable-blink-features=AutomationControlled",
                                  "--window-size=1280,900"])
                        ctx.add_init_script(STEALTH_JS)
                        if cookies:
                            try:
                                ctx.add_cookies(cookies)
                            except Exception as e:
                                print("[fresh] cookie warn", str(e)[:80], flush=True)
                    page = ctx.pages[0] if ctx.pages else ctx.new_page()
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(5000)
                    dismiss_consent(page)
                    raw = page.evaluate("() => window.__NEXT_DATA__ ? JSON.stringify(window.__NEXT_DATA__) : null")
                    entries = extract_entries(json.loads(raw)) if raw else []
                    rendered = real_hrefs(page)  # id-slug tokens actually rendered => 200-able
                    for e in entries:
                        try:
                            jid = str(e.get("id"))
                            slug = (e.get("slug") or "").strip().strip("/")
                            if not jid or not slug:
                                continue
                            token = f"{jid}-{slug}"
                            # Only emit jobs whose URL is actively rendered (avoids 422 stale/closed listings)
                            if rendered and token not in rendered:
                                continue
                            link = f"https://wellfound.com/jobs/{token}"
                            fresh[link] = {
                                "title": (e.get("title") or e.get("primaryRoleTitle") or slug).strip()[:120],
                                "link": link,
                                "posted_at": int(e.get("liveStartAt")),
                            }
                        except Exception:
                            continue
                    print(f"[fresh] {url.replace('https://wellfound.com','')} → {len(entries)} entries, {len(rendered)} rendered (uniq={len(fresh)})", flush=True)
                    page.wait_for_timeout(800)
                except Exception as e:
                    print(f"[fresh] ERR {url} {str(e)[:110]}", flush=True)

        if ctx:
            try:
                ctx.close()
            except Exception:
                pass

    # Final 200-verify pass: emit only jobs that return HTTP 200 RIGHT NOW.
    # Wellfound closes listings within days-to-hours; render-verified is necessary
    # but not sufficient — probe status live so the injected pool is ~all apply-able.
    if fresh:
        verify_ctx = None
        try:
            verify_ctx = p.chromium.launch_persistent_context(
                user_data_dir="/tmp/cloak_profile_wf_verify", executable_path=CLOAK, headless=True,
                args=["--no-first-run", "--no-default-browser-check",
                      "--disable-blink-features=AutomationControlled", "--window-size=1280,900"])
            try:
                verify_ctx.add_cookies(cookies)
            except Exception:
                pass
            vpage = verify_ctx.pages[0] if verify_ctx.pages else verify_ctx.new_page()
            vpage.goto("about:blank")
            live = {}
            for k, v in fresh.items():
                try:
                    vpage.goto(v["link"], wait_until="domcontentloaded", timeout=20000)
                    if vpage.url and vpage.url.startswith("https://wellfound.com/jobs/"):
                        live[k] = v  # if it rendered a job page, it's 200-able
                except Exception:
                    pass
                time.sleep(0.3)
            fresh = live
            print(f"[fresh] post-verify live={len(fresh)}", flush=True)
        except Exception as e:
            print(f"[fresh] verify err {str(e)[:100]}", flush=True)
        finally:
            try:
                if verify_ctx:
                    verify_ctx.close()
            except Exception:
                pass

    now = time.time()
    cutoff = now - FRESH_DAYS * 86400
    keep = [v for v in fresh.values() if v["posted_at"] >= cutoff]
    if len(keep) < MIN_KEEP:
        cutoff = now - RELAX_DAYS * 86400
        keep = [v for v in fresh.values() if v["posted_at"] >= cutoff]
    keep = [v for v in keep if v["link"] not in seen_ever]
    keep = sorted(keep, key=lambda x: -x["posted_at"])[:MAX_KEEP]
    for j in keep:
        j["posted_iso"] = time.strftime("%Y-%m-%d", time.gmtime(j["posted_at"]))

    json.dump([{"site": "wellfound_fresh", "count": len(keep), "jobs": keep}], open(OUT, "w"), indent=1)
    print(f"[wellfound_fresh] {len(keep)} new fresh jobs (seen={len(fresh)})", flush=True)
    print(f"saved {OUT}")
    conn.close()


if __name__ == "__main__":
    main()