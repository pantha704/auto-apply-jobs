#!/home/ubuntu/browser-use-venv/bin/python
"""Read-only Browser Use analyzer for a worker-owned CloakBrowser CDP session."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

# Private local analysis: never emit Browser Use telemetry or cloud traces.
os.environ["ANONYMIZED_TELEMETRY"] = "false"
os.environ["BROWSER_USE_CLOUD_SYNC"] = "false"

from pydantic import BaseModel


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
    from browser_use import Agent, Browser, ChatOpenAI

    model = os.environ.get("BROWSER_USE_MODEL", "openai/nvidia/llama-3.3-nemotron-super-49b-v1.5")
    base_url = os.environ.get("BROWSER_USE_LLM_BASE_URL", "http://127.0.0.1:4000/v1")
    api_key = (
        os.environ.get("BROWSER_USE_LLM_API_KEY")
        or os.environ.get("LITELLM_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or "local-router"
    )
    candidates = request.get("candidates") or []
    allowed_ids = [item.get("candidate_id") for item in candidates]
    task = (
        "READ-ONLY ANALYSIS. Do not click, type, scroll, navigate, submit, send, or change the page. "
        "Inspect the current complex job-application page as visual/DOM context. Choose at most one "
        "candidate_id from the supplied sanitized inventory that best matches the requested low-risk "
        "navigation intent. If uncertain, if the page asks for credentials/CAPTCHA, or if the action "
        "could submit/send/finalize an application, return candidate_id=null.\n"
        + json.dumps(
            {
                "intent": request.get("intent"),
                "allowed_candidate_ids": allowed_ids,
                "candidates": candidates,
            },
            separators=(",", ":"),
        )
    )
    browser = Browser(cdp_url=cdp_url, keep_alive=True)
    llm = ChatOpenAI(model=model, base_url=base_url, api_key=api_key)
    agent = Agent(
        task=task,
        llm=llm,
        browser=browser,
        tools=read_only_tools(),
        output_model_schema=Proposal,
        use_vision=True,
        max_actions_per_step=1,
    )
    history = await agent.run(max_steps=2)
    structured = history.structured_output
    if structured is not None:
        return Proposal.model_validate(structured)
    final = history.final_result()
    if not final:
        return Proposal(candidate_id=None)
    return Proposal.model_validate_json(final)


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
