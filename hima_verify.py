"""Verify the Himalayas session saved by hima_sso_wait.py actually authenticates."""
import json, sys
from playwright.sync_api import sync_playwright

PORTAL = "/home/ubuntu/job_hunt_linkedin/portal_himalayas.json"
with open(PORTAL) as f:
    data = json.load(f)
cookies = data if isinstance(data, list) else data.get("cookies", [])
print(f"cookies loaded: {len(cookies)}")
print("domains:", sorted({c.get("domain", "?") for c in cookies}))
print("names:", sorted({c.get("name", "?") for c in cookies}))

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path="/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome", args=["--no-sandbox"])
    ctx = browser.new_context()
    ctx.add_cookies(cookies)
    page = ctx.new_page()
    page.goto("https://himalayas.app/", timeout=60000, wait_until="domcontentloaded")
    page.wait_for_timeout(6000)
    print("URL:", page.url)
    print("TITLE:", page.title())
    body = page.inner_text("body")[:2000]
    print("BODY:")
    print(body)
    low = body.lower()
    checks = {
        "sign_in_link": ("sign in" in low or "log in" in low),
        "signup_prompt": "sign up" in low,
        "user_name": "pratham" in low or "jaiswal" in low,
        "talent_area": "talent" in low,
        "dashboard": "dashboard" in low,
        "cloudflare": "cloudflare" in low or "just a moment" in low,
    }
    print("CHECKS:", json.dumps(checks, indent=2))
    browser.close()
