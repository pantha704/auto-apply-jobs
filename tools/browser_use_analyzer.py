#!/home/ubuntu/browser-use-venv/bin/python
"""Read-only Browser Use analyzer for a worker-owned CloakBrowser CDP session."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path

# Private local analysis: never emit Browser Use telemetry or cloud traces.
os.environ["ANONYMIZED_TELEMETRY"] = "false"
os.environ["BROWSER_USE_CLOUD_SYNC"] = "false"

from pydantic import BaseModel

# F09 fix: Redaction patterns before provider boundary
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d ()-]{8,}\d)(?!\d)")
_TOKEN_RE = re.compile(r"(?i)(?:bearer\s+|token[=:]\s*|cookie[=:]\s*)[^\s,;]+")
_URL_RE = re.compile(r"https?://\S+")
_NUMBER_RE = re.compile(r"\b\d{4,8}\b")


def _redact_pii(text: str) -> str:
    """Redact PII before sending to any external provider."""
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
    text = _TOKEN_RE.sub("[REDACTED_TOKEN]", text)
    text = _URL_RE.sub("[REDACTED_URL]", text)
    text = _NUMBER_RE.sub("[REDACTED_NUMBER]", text)
    return text[:500]  # Truncate for safety


def _redact_candidates(candidates: list[dict]) -> list[dict]:
    """Redact PII from candidate labels before LLM boundary."""
    redacted = []
    for item in candidates:
        redacted.append({
            "candidate_id": item.get("candidate_id"),
            "role": item.get("role"),
            "label": _redact_pii(item.get("label", "")),
        })
    return redacted


class Proposal(BaseModel):
    candidate_id: str | None


def read_only_tools():
    from browser_use import Tools
    
    tools = Tools(output_model=Proposal)
    for action_name in tuple(tools.registry.registry.actions):
        if action_name != "done":
            tools.exclude_action(action_name)
    remaining = set(tools.registry.registry.actions)
    if remaining != {"done"}:
        raise RuntimeError(f"unsafe Browser Use tool registry: {sorted(remaining)}")
    return tools


async def analyze(cdp_url: str, request: dict) -> Proposal:
    # R8: inventory-only. No live browser, no DOM, no Agent.
    # cdp_url accepted for API compat but never dialed.
    _ = cdp_url
    candidates = request.get("candidates") or []
    intent = str(request.get("intent") or "").lower()
    if intent in {"submit", "send", "finalize", "confirm"}:
        return Proposal(candidate_id=None)
    allowed = {c.get("candidate_id") for c in candidates if c.get("candidate_id")}
    if len(allowed) != 1:
        return Proposal(candidate_id=None)
    only = next(iter(allowed))
    label = ""
    for c in candidates:
        if c.get("candidate_id") == only:
            label = str(c.get("label") or "")
    if any(k in label.lower() for k in ("submit", "send", "final", "confirm", "apply")):
        return Proposal(candidate_id=None)
    return Proposal(candidate_id=only)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cdp-url")
    parser.add_argument("--input")
    parser.add_argument("--output")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps({"actions": sorted(read_only_tools().registry.registry.actions)}))
        return 0
    if not all((args.cdp_url, args.input, args.output)):
        parser.error("--cdp-url, --input, and --output are required")
    request = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if request.get("schema_version") != 1:
        raise ValueError("unsupported recovery request schema")
    proposal = asyncio.run(analyze(args.cdp_url, request))
    target = Path(args.output)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(proposal.model_dump_json(), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
