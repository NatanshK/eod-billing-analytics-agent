"""Runtime configuration, all of it environment-driven."""

from __future__ import annotations

import os

API_PREFIX = "/api/v1"

DEFAULT_TOP_N = 5
MAX_TOP_N = 20

#: A clinic-day is kilobytes, so anything near this is a mistake. Rejecting early
#: beats parsing megabytes of JSON to find out.
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", 5 * 1024 * 1024))


def allowed_origins() -> list[str]:
    """CORS origins. Defaults to the local Vite dev server."""
    raw = os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    if raw.strip() == "*":
        return ["*"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


#: Display names for known clinics. A production system would keep this in a
#: clinics table — the billing log carries only an id. Unknown ids fall back to
#: the id itself rather than inventing a name.
CLINIC_DIRECTORY: dict[str, dict[str, str]] = {
    "CLN-KNP-014": {
        "name": "Mehta Multi-Specialty Clinic",
        "location": "Kanpur, Uttar Pradesh",
        "owner": "Dr. Anand Mehta",
    },
    # Used by the hand-computed test fixtures.
    "CLINIC-MEHTA-001": {
        "name": "Mehta Multi-Specialty Clinic",
        "location": "Kanpur, Uttar Pradesh",
        "owner": "Dr. Anand Mehta",
    },
}


def clinic_profile(clinic_id: str) -> dict[str, str]:
    known = CLINIC_DIRECTORY.get(clinic_id)
    if known:
        return {"clinic_id": clinic_id, **known}
    return {"clinic_id": clinic_id, "name": clinic_id, "location": "", "owner": ""}
