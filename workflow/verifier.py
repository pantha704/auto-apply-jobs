from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

from .models import SubmissionEvidence


@dataclass(frozen=True)
class Observation:
    url: str
    success_text: str | None
    application_id: str | None
    submitted_control_clicked: bool


class SubmissionVerifier:
    """Turn deterministic portal observations into auditable evidence."""

    SUCCESS_PHRASES = (
        "application submitted",
        "application received",
        "thanks for applying",
        "thank you for applying",
    )

    def verify(self, observation: Observation) -> SubmissionEvidence | None:
        if not observation.submitted_control_clicked:
            return None
        parsed = urlparse(observation.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        text = (observation.success_text or "").casefold().strip()
        text_signal = any(phrase in text for phrase in self.SUCCESS_PHRASES)
        application_id = (observation.application_id or "").strip()
        id_signal = bool(application_id)
        confirmation_url = any(
            marker in parsed.path.casefold()
            for marker in ("confirmation", "submitted", "applications/")
        )
        if not text_signal and not (id_signal and confirmation_url):
            return None
        return SubmissionEvidence(
            observed_at=datetime.now(timezone.utc).isoformat(),
            success_text=observation.success_text if text_signal else None,
            application_id=application_id if id_signal else None,
        )
