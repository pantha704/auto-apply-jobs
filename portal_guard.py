#!/usr/bin/env python3
"""Bounded, read-only authentication probes for canonical portal sessions.

This process never logs in, captures state, stages candidates, or promotes revisions.
It loads each immutable current revision into an isolated browser context, performs a
server-accepted endpoint probe, and records only privacy-safe health metadata.
"""
from __future__ import annotations

import fcntl
import os
import time
from pathlib import Path

from workflow.portal_session_runtime import session_manager
from workflow.portal_sessions import classify_probe

HERE = Path(__file__).resolve().parent
CLOAK = os.getenv(
    "JOBHUNT_BROWSER_EXECUTABLE",
    "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome",
)
LOG = HERE / "logs" / "portal_guard.log"
LOCK = Path("/tmp/jobhunt-session-health-probe.lock")

PROBES = {
    "linkedin": ("https://www.linkedin.com/feed/", "linkedin.com", "/feed"),
    "wellfound": ("https://wellfound.com/settings", "wellfound.com", "/settings"),
    "internshala": (
        "https://internshala.com/student/dashboard",
        "internshala.com",
        "student",
    ),
    "yc": (
        "https://www.workatastartup.com/applications",
        "workatastartup.com",
        "/applications",
    ),
    "himalayas": ("https://himalayas.app/", "himalayas.app", ""),
}


def log(message: str) -> None:
    LOG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _portal_state(portal: str, url: str, title: str, body: str, error: bool) -> str:
    state = classify_probe(url, title, body, network_error=error)
    if state != "valid":
        return state
    _, expected_host, expected_path = PROBES[portal]
    low_url = url.lower()
    if expected_host not in low_url or expected_path not in low_url:
        return "unknown"
    if portal == "himalayas":
        low_body = body.lower()
        if "verify you are human" in low_body or "security verification" in low_body:
            return "challenged"
        if "log in" in low_body and "log out" not in low_body:
            return "expired"
    return "valid"


def probe_one(browser, manager, portal: str) -> str:
    try:
        snapshot = manager.load_current(portal)
    except Exception:
        manager.record_health(portal, "unknown", "no-current-revision")
        return "unknown"

    context = None
    state = "unknown"
    detail = "probe-error"
    try:
        context = browser.new_context(storage_state=snapshot.state)
        page = context.new_page()
        page.set_default_timeout(15000)
        page.set_default_navigation_timeout(35000)
        target = PROBES[portal][0]
        page.goto(target, wait_until="domcontentloaded", timeout=35000)
        try:
            body = page.inner_text("body")[:5000]
        except Exception:
            body = ""
        state = _portal_state(portal, page.url, page.title(), body, False)
        detail = {
            "valid": "authenticated-endpoint-accepted",
            "expired": "authentication-required",
            "challenged": "challenge-detected",
            "unknown": "probe-inconclusive",
        }[state]
    except Exception:
        state, detail = "unknown", "probe-network-error"
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
    manager.record_health(portal, state, detail)
    return state


def main() -> int:
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("w") as lock_handle:
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("another health probe is already running")
            return 0
        manager = session_manager()
        from playwright.sync_api import sync_playwright

        results = {}
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=CLOAK,
                headless=True,
                args=[
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            try:
                for portal in PROBES:
                    results[portal] = probe_one(browser, manager, portal)
                    log(f"{portal}: {results[portal]}")
            finally:
                browser.close()
        invalid = [name for name, state in results.items() if state != "valid"]
        log("probe complete" if not invalid else f"attention required: {','.join(invalid)}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
