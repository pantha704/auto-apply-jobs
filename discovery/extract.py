from __future__ import annotations

ALIASES = {
    "company": ("company name", "company"),
    "website": ("website", "link to website", "site"),
    "careers_url": ("careers page", "career page", "jobs page", "careers"),
    "apply_url": ("job url", "job link", "apply url", "listing", "apply"),
    "email": ("contact email", "hiring email", "e-mail", "email"),
    "role": ("typical tech roles", "role", "title"),
    "location": ("hq country", "remote policy", "location", "hq"),
    "requirements": ("what they do", "requirements", "notes"),
}


def _norm(header: str) -> str:
    return " ".join(str(header or "").strip().lower().split())


def map_headers(headers: list[str]) -> dict[str, str]:
    """Map spreadsheet headers to logical fields. Data-driven aliases, not per-sheet code."""
    original = { _norm(h): h for h in headers if str(h).strip() }
    mapping: dict[str, str] = {}
    for field, aliases in ALIASES.items():
        for alias in aliases:
            if alias in original:
                mapping[field] = original[alias]
                break
    if "company" not in mapping:
        for key in ("name", "startup", "startup name"):
            if key in original:
                mapping["company"] = original[key]
                break
    return mapping
