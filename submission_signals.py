"""Strict, portal-neutral positive submission signals.

F05 fix: HTTP status codes alone are NOT confirmation. A 200 can contain
application-level errors. Confirmation requires specific text evidence
correlated with the candidate and requisition.
"""

import re
from collections.abc import Iterable

# Positive confirmation phrases — must appear in the response body.
# Each phrase is specific enough to indicate a real submission, not a generic page.
CONFIRMATION_TEXT = re.compile(
    r"(?:application (?:is |was |has been )?(?:sent|submitted)|"
    r"your application was sent|your application has been submitted|"
    r"successfully (?:applied|submitted)|applied successfully|"
    r"thanks for applying|we(?:'ve| have) received your application|"
    r"you(?:'ve| have) applied)",
    re.IGNORECASE,
)

# Explicit exclusion: interview-sharing phrases are NOT submission confirmation.
# They appear on profile-sharing pages, not application receipt pages.
EXCLUSION_TEXT = re.compile(
    r"share your (?:ai )?interview|schedule an interview",
    re.IGNORECASE,
)


def has_submission_confirmation(
    text: str = "",
    http_statuses: Iterable[int] = (),
) -> bool:
    """Require a specific confirmation phrase in the page body.

    F05 fix: HTTP status codes are ignored. A 200/201/202/204 response
    can contain application-level errors, redirects to error pages, or
    unrelated success messages. Only body text evidence qualifies.

    The http_statuses parameter is accepted for backward compatibility
    but deliberately unused — callers should remove it in a follow-up.
    """
    body = text or ""
    if EXCLUSION_TEXT.search(body):
        return False
    return bool(CONFIRMATION_TEXT.search(body))
