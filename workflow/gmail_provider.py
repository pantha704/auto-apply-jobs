from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable


class GmailProviderError(RuntimeError):
    pass


class GmailApiProvider:
    """Official Gmail API adapter using the maintained Google Workspace wrapper."""

    def __init__(self, token_path: str | Path, api_script: str | Path, *,
                 runner: Callable[..., object] = subprocess.run):
        self.token_path = Path(token_path)
        self.api_script = Path(api_script)
        self.runner = runner

    def is_ready(self) -> bool:
        return self.token_path.is_file() and self.api_script.is_file()

    def send(self, *, to: str, subject: str, body: str) -> str:
        if not self.is_ready():
            raise GmailProviderError("provider_not_authenticated")
        env = os.environ.copy()
        env["HERMES_HOME"] = str(self.token_path.parent)
        result = self.runner(
            [sys.executable, str(self.api_script), "gmail", "send",
             "--to", to, "--subject", subject, "--body", body],
            capture_output=True,
            text=True,
            timeout=90,
            shell=False,
            env=env,
        )
        if int(getattr(result, "returncode", 1)) != 0:
            raise GmailProviderError("gmail_api_send_failed")
        try:
            payload = json.loads(str(getattr(result, "stdout", "")))
            provider_id = str(payload["id"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise GmailProviderError("gmail_api_invalid_response") from exc
        if not provider_id:
            raise GmailProviderError("gmail_api_missing_message_id")
        return provider_id
