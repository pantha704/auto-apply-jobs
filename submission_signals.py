"""Strict, portal-neutral positive submission signals."""

import re
from collections.abc import Iterable


CONFIRMATION_TEXT = re.compile(
    r"(?:application (?:is |was |has been )?(?:sent|submitted)|"
    r"your application was sent|your application has been submitted|"
    r"successfully (?:applied|submitted)|applied successfully|"
    r"thanks for applying|we(?:'ve| have) received your application|"
    r"you(?:'ve| have) applied|share your ai interview)",
    re.IGNORECASE,
)


def has_submission_confirmation(text: str = "", http_statuses: Iterable[int] = ()) -> bool:
    """Require a specific confirmation phrase or an already-scoped 2xx request."""
    return any(status in (200, 201, 202, 204) for status in http_statuses) or bool(
        CONFIRMATION_TEXT.search(text or "")
    )
