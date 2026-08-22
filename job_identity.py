"""Stable cross-process identity for harvested jobs."""

from hashlib import sha256
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_KEYS = {
    "currentjobid", "e_bp", "fbclid", "from", "gclid", "lipi",
    "originalsubdomain", "pagenum", "position", "ref", "refid",
    "searchid", "shareid", "trackingid", "trk",
}


def canonical_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        scheme = (parts.scheme or "https").lower()
        host = parts.netloc.lower()
        path = parts.path.rstrip("/") or "/"
        # LinkedIn job identity is entirely in /jobs/view/<id>; its query is
        # navigation/tracking state and must never create another queue row.
        if host.endswith("linkedin.com"):
            query = ""
        else:
            kept = []
            for key, value in parse_qsl(parts.query, keep_blank_values=True):
                low = key.lower()
                if low.startswith("utm_") or low in TRACKING_KEYS:
                    continue
                kept.append((key, value))
            query = urlencode(sorted(kept))
        return urlunsplit((scheme, host, path, query, ""))
    except Exception:
        return raw.split("#", 1)[0].rstrip("/")


def stable_job_id(source: str, url: str) -> str:
    identity = f"{(source or 'job').lower()}\0{canonical_url(url)}"
    digest = sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"{(source or 'job').lower()}-{digest}"
