from __future__ import annotations

from urllib.parse import urlparse

ATS_HOST_MARKERS = (
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "myworkdayjobs.com",
    "smartrecruiters.com",
)

JOB_PATH_MARKERS = ("/jobs/", "/job/", "/jobs/view/")


def _clean(value: object) -> str:
    return str(value or "").strip()


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _is_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def _valid_email(value: str) -> bool:
    if value.lower().startswith("mailto:"):
        value = value[7:]
    if "@" not in value or " " in value:
        return False
    local, _, domain = value.partition("@")
    return bool(local) and "." in domain


def _is_ats(url: str) -> bool:
    host = _host(url)
    return any(host == marker or host.endswith("." + marker) for marker in ATS_HOST_MARKERS)


def _looks_like_job_listing(url: str) -> bool:
    if not _is_http_url(url):
        return False
    path = (urlparse(url).path or "").lower()
    if any(marker in path for marker in JOB_PATH_MARKERS):
        return True
    host = _host(url)
    if host == "weworkremotely.com" or host.endswith(".weworkremotely.com"):
        return "/remote-jobs/" in path
    return "linkedin.com" in host and "/jobs/view" in path


def classify_row(row: dict) -> list[dict]:
    """Return zero or more routes for one extracted spreadsheet row."""
    email = _clean(row.get("email"))
    apply_url = _clean(row.get("apply_url"))
    careers_url = _clean(row.get("careers_url"))
    website = _clean(row.get("website"))
    routes: list[dict] = []

    if _valid_email(email):
        routes.append({"routed": "cold_email"})

    if apply_url:
        if _is_ats(apply_url):
            routes.append({"routed": "review", "reason": "ats_needs_adapter"})
        elif _looks_like_job_listing(apply_url):
            routes.append({"routed": "apply_candidate"})

    if not apply_url:
        target = careers_url if len(careers_url) > 8 else website
        if len(target) > 8 and _is_http_url(target):
            routes.append({"routed": "watchlist"})

    if not routes and any(_clean(row.get(k)) for k in ("company", "role", "requirements")):
        routes.append({"routed": "review", "reason": "unknown"})
    return routes
