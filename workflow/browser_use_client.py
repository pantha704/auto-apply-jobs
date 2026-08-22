from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .browser_runtime import require_loopback_cdp
from .providers import RecoveryProvider
from .recovery import RecoveryRequest, TraceAction


_ALLOWED_ENV = {
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "BROWSER_USE_MODEL",
    "BROWSER_USE_LLM_BASE_URL",
    "BROWSER_USE_LLM_API_KEY",
    "OPENAI_API_KEY",
    "LITELLM_API_KEY",
}


def _run_sidecar(
    command: Sequence[str],
    *,
    input_path: str,
    output_path: str,
    timeout: int,
    env: dict[str, str],
) -> None:
    del input_path, output_path
    completed = subprocess.run(
        tuple(command),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("Browser Use analysis sidecar failed")


class BrowserUseSidecar(RecoveryProvider):
    """Read-only Browser Use analysis attached to a worker-owned Cloak CDP port."""

    def __init__(
        self,
        cdp_url: str,
        *,
        python: str = "/home/ubuntu/browser-use-venv/bin/python",
        script: str | Path | None = None,
        timeout: int = 90,
        run: Callable[..., None] = _run_sidecar,
        temp_root: str | Path | None = None,
    ) -> None:
        self.cdp_url = require_loopback_cdp(cdp_url)
        self.python = python
        self.script = str(
            script
            or Path(__file__).parents[1] / "tools" / "browser_use_analyzer.py"
        )
        self.timeout = timeout
        self.run = run
        self.temp_root = Path(temp_root) if temp_root is not None else None

    @staticmethod
    def _payload(request: RecoveryRequest) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "site_id": request.site_id,
            "intent": request.intent,
            "page_fingerprint": request.page_fingerprint,
            "candidates": [
                {
                    "candidate_id": item.candidate_id,
                    "role": item.role,
                    "label": item.label,
                }
                for item in request.candidates
            ],
        }

    def recover(self, request: RecoveryRequest) -> Sequence[TraceAction]:
        root = tempfile.mkdtemp(prefix="jobhunt-bu-", dir=self.temp_root)
        os.chmod(root, 0o700)
        input_path = Path(root) / "request.json"
        output_path = Path(root) / "result.json"
        try:
            input_path.write_text(json.dumps(self._payload(request)), encoding="utf-8")
            os.chmod(input_path, 0o600)
            command = (
                self.python,
                self.script,
                "--cdp-url",
                self.cdp_url,
                "--input",
                str(input_path),
                "--output",
                str(output_path),
            )
            env = {key: value for key, value in os.environ.items() if key in _ALLOWED_ENV}
            self.run(
                command,
                input_path=str(input_path),
                output_path=str(output_path),
                timeout=self.timeout,
                env=env,
            )
            if not output_path.is_file():
                raise RuntimeError("Browser Use analysis produced no result")
            result = json.loads(output_path.read_text(encoding="utf-8"))
            if not isinstance(result, dict) or set(result) != {"candidate_id"}:
                raise ValueError("invalid Browser Use result schema")
            candidate_id = result["candidate_id"]
            if candidate_id is None:
                return ()
            by_id = {item.candidate_id: item for item in request.candidates}
            if candidate_id not in by_id:
                raise ValueError("Browser Use selected an unknown candidate")
            candidate = by_id[candidate_id]
            return (
                TraceAction("click", request.intent, candidate_id, candidate.role),
            )
        finally:
            shutil.rmtree(root, ignore_errors=True)
