from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path


class FileSessionLease:
    """Cross-process exclusive lease for one browser session/CDP endpoint."""

    def __init__(self, root: str | Path = "/tmp/jobhunt-browser-leases") -> None:
        self.root = Path(root)

    @contextmanager
    def acquire(self, session_id: str):
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in session_id)[:120]
        if not safe:
            raise ValueError("session_id is required")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.root / f"{safe}.lock"
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
