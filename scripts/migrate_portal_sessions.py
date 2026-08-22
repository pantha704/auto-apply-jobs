#!/usr/bin/env python3
"""One-time private legacy-session import with candidate/probe/promotion semantics.

The utility never prints state material and never deletes or modifies legacy sources.
A candidate is promoted only after a bounded authenticated endpoint accepts it.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
from pathlib import Path

from portal_guard import PROBES, _portal_state
from workflow.portal_session_runtime import session_manager
from workflow.schema import migrate_control

ROOT = Path(__file__).resolve().parents[1]
LEGACY = {
    "linkedin": ROOT / "li_state.json",
    "wellfound": ROOT / "portal_wellfound.json",
    "internshala": ROOT / "portal_internshala.json",
    "yc": ROOT / "portal_yc.json",
    "himalayas": ROOT / "portal_himalayas.json",
}
CLOAK = os.getenv(
    "JOBHUNT_BROWSER_EXECUTABLE",
    "/home/ubuntu/.cloakbrowser/chromium-146.0.7680.177.5/chrome",
)


def load_private_state(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError("legacy source is not a regular file")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("legacy state is not an object")
    cookies = []
    for cookie in payload.get("cookies", []):
        if not isinstance(cookie, dict):
            continue
        if all(isinstance(cookie.get(k), str) and cookie.get(k) for k in ("name", "value", "domain")):
            cookies.append(cookie)
    origins = [item for item in payload.get("origins", []) if isinstance(item, dict) and isinstance(item.get("origin"), str)]
    if not cookies and not origins:
        raise ValueError("legacy state has no usable material")
    return {"cookies": cookies, "origins": origins}


def probe_candidate(browser, portal: str, state: dict) -> tuple[str, str]:
    context = None
    try:
        context = browser.new_context(storage_state=state)
        page = context.new_page()
        page.set_default_timeout(15000)
        page.set_default_navigation_timeout(35000)
        page.goto(PROBES[portal][0], wait_until="domcontentloaded", timeout=35000)
        try:
            body = page.inner_text("body")[:5000]
        except Exception:
            body = ""
        outcome = _portal_state(portal, page.url, page.title(), body, False)
        detail = {
            "valid": "authenticated-endpoint-accepted",
            "expired": "authentication-required",
            "challenged": "challenge-detected",
            "unknown": "probe-inconclusive",
        }[outcome]
        return outcome, detail
    except Exception:
        return "unknown", "probe-network-error"
    finally:
        if context is not None:
            context.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("portals", nargs="*")
    parser.add_argument("--no-promote", action="store_true")
    args = parser.parse_args()
    unknown = sorted(set(args.portals) - set(LEGACY))
    if unknown:
        parser.error(f"unknown portal(s): {', '.join(unknown)}")
    portals = args.portals or list(LEGACY)

    control_db = Path(os.getenv("JOBHUNT_CONTROL_DB", ROOT / "controlplane.db"))
    applied = migrate_control(control_db)
    print(f"control migrations applied: {applied}", flush=True)
    manager = session_manager()

    from playwright.sync_api import sync_playwright

    outcomes = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=CLOAK,
            headless=True,
            args=["--no-first-run", "--no-default-browser-check"],
        )
        try:
            for portal in portals:
                source = LEGACY[portal]
                if not source.exists():
                    outcomes[portal] = "source-missing"
                    continue
                lease = None
                try:
                    state = load_private_state(source)
                except (ValueError, json.JSONDecodeError):
                    outcomes[portal] = "source-unusable"
                    continue
                try:
                    lease = manager.acquire_renewal(
                        portal,
                        f"legacy-import:{socket.gethostname()}:{os.getpid()}",
                        ttl_seconds=300,
                    )
                    candidate = manager.stage_candidate(portal, state, lease.token)
                    outcome, detail = probe_candidate(browser, portal, state)
                    manager.record_probe(portal, candidate.id, outcome, lease.token, detail)
                    if outcome == "valid" and not args.no_promote:
                        promoted = manager.promote(portal, candidate.id, lease.token)
                        outcomes[portal] = f"promoted-revision-{promoted.revision}"
                    elif outcome == "valid":
                        outcomes[portal] = "validated-candidate-not-promoted"
                    else:
                        outcomes[portal] = f"candidate-{outcome}"
                except Exception as exc:
                    outcomes[portal] = f"failed-{type(exc).__name__}"
                finally:
                    if lease is not None:
                        try:
                            manager.release_renewal(portal, lease.token)
                        except Exception:
                            pass
        finally:
            browser.close()

    for portal in portals:
        print(f"{portal}: {outcomes[portal]}", flush=True)
    return 0 if all(not value.startswith("failed-") for value in outcomes.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
