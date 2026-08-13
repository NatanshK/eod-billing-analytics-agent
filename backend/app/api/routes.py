"""REST endpoints.

Route handlers stay thin on purpose: they resolve arguments, call into the core
or storage layer, and shape the response. Every failure mode they can produce is
mapped to a typed exception in :mod:`app.core.errors`, which
:mod:`app.main` turns into a structured response — so no bad input path ends in a
generic 500.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from ..config import API_PREFIX, MAX_TOP_N, MAX_UPLOAD_BYTES, clinic_profile
from ..core.errors import BillingLogError, DayNotFoundError, RowError
from ..core.parsing import extract_rows, load_payload, parse_billing_log
from ..narrative.provider import default_provider
from ..narrative.service import generate_narrative
from ..storage import repository as repo
from . import reports

router = APIRouter(prefix=API_PREFIX)


# --------------------------------------------------------------------------
# Ingest
# --------------------------------------------------------------------------


async def _read_payload(request: Request) -> Any:
    """Accept the log as a JSON body or as an uploaded file, indifferently.

    A clinic exports a file; a test or an integration posts JSON. Supporting
    both costs one branch and saves the caller from caring.
    """
    content_type = (request.headers.get("content-type") or "").lower()

    if "multipart/form-data" in content_type:
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise BillingLogError(
                "No file was included in the upload",
                code="missing_file",
                errors=[
                    RowError(
                        row_index=-1,
                        code="missing_file",
                        message="Expected a form field named 'file'",
                        hint="Send the log as multipart/form-data with the field name 'file'.",
                    )
                ],
            )
        raw = await upload.read()
    else:
        raw = await request.body()

    if len(raw) > MAX_UPLOAD_BYTES:
        raise BillingLogError(
            f"Billing log is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit",
            code="payload_too_large",
            errors=[
                RowError(
                    row_index=-1,
                    code="payload_too_large",
                    message=f"Received {len(raw)} bytes",
                    hint="A single clinic-day should be a few hundred kilobytes at most.",
                )
            ],
        )

    return load_payload(raw)


@router.post("/billing-logs", status_code=status.HTTP_201_CREATED)
async def ingest_billing_log(
    request: Request,
    strict: bool = Query(
        False,
        description=(
            "Reject the entire day if any row is invalid. Default false: the valid "
            "rows are ingested and the rejects are stored and reported with the day."
        ),
    ),
    clinic_id: str | None = Query(
        None, description="Required only when the log has no rows to infer it from"
    ),
    business_date: str | None = Query(
        None, description="YYYY-MM-DD. Required only for a day with no rows"
    ),
) -> dict[str, Any]:
    """Validate and store one clinic-day.

    A bad row does not sink the day. It is quarantined, stored beside the day and
    echoed in *every* report response for it, so the totals are visibly "18 of 19
    rows" rather than quietly short. ``strict=true`` gets all-or-nothing instead.

    A day with no rows at all is a real thing — a closed clinic — but it carries
    no clinic or date to file it under, so those must be supplied explicitly.
    """
    payload = await _read_payload(request)
    rows = extract_rows(payload)
    parsed = parse_billing_log(rows)

    if parsed.errors and strict:
        raise BillingLogError(
            f"{len(parsed.errors)} of {parsed.rows_seen} row(s) could not be ingested",
            errors=parsed.errors,
            valid_rows=len(parsed.visits),
        )

    rejected = [e.to_dict() for e in parsed.errors]
    parsed.errors = []  # what remains is exactly what will be stored

    _apply_explicit_identity(parsed, clinic_id, business_date, rejected)

    record = repo.save_day(parsed, rows, rejected=rejected, rows_received=parsed.rows_seen)

    return {
        **record.to_dict(),
        "clinic_name": clinic_profile(record.clinic_id)["name"],
        "visits_ingested": len(parsed.visits),
        "strict": strict,
    }


def _apply_explicit_identity(
    parsed, clinic_id: str | None, business_date: str | None, rejected: list[dict[str, Any]]
) -> None:
    """Resolve which clinic and date this upload belongs to.

    Normally both are read off the rows. An empty log has no rows to read, so the
    caller supplies them; and when the caller supplies them alongside real rows,
    a disagreement is a filing error worth catching rather than overriding.
    """
    if business_date is not None:
        try:
            supplied_date = date.fromisoformat(business_date)
        except ValueError:
            raise BillingLogError(
                f"business_date '{business_date}' is not a valid date",
                code="invalid_business_date",
                errors=[
                    RowError(
                        row_index=-1,
                        code="invalid_business_date",
                        message=f"'{business_date}' could not be read as a date",
                        field="business_date",
                        hint="Use YYYY-MM-DD, e.g. 2026-07-26.",
                    )
                ],
            ) from None
    else:
        supplied_date = None

    if not parsed.visits:
        if not clinic_id or supplied_date is None:
            raise BillingLogError(
                "This log has no visits, so the clinic and date cannot be inferred from it",
                code="unattributable_empty_log",
                errors=[
                    RowError(
                        row_index=-1,
                        code="unattributable_empty_log",
                        message="An empty billing log carries no clinic_id or timestamp",
                        hint=(
                            "Record a day with no visits by passing them explicitly: "
                            "?clinic_id=CLN-KNP-014&business_date=2026-07-26"
                        ),
                    )
                ],
                valid_rows=0,
            )
        parsed.clinic_id = clinic_id
        parsed.business_date = supplied_date
        return

    for supplied, actual, field_name in (
        (clinic_id, parsed.clinic_id, "clinic_id"),
        (supplied_date, parsed.business_date, "business_date"),
    ):
        if supplied is not None and supplied != actual:
            raise BillingLogError(
                f"The log's {field_name} does not match the one supplied",
                code=f"{field_name}_mismatch",
                errors=[
                    RowError(
                        row_index=-1,
                        code=f"{field_name}_mismatch",
                        message=f"Rows say '{actual}' but the request said '{supplied}'",
                        field=field_name,
                        hint="Drop the query parameter to use the value in the rows.",
                    )
                ],
                valid_rows=len(parsed.visits),
            )


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


@router.get("/clinics")
def list_clinics() -> dict[str, Any]:
    return {
        "clinics": [
            {**clinic_profile(cid), "days": len(repo.list_days(cid))}
            for cid in repo.list_clinics()
        ]
    }


@router.get("/clinics/{clinic_id}/days")
def list_days(clinic_id: str) -> dict[str, Any]:
    """Ingested dates for a clinic, newest first — drives the date picker."""
    days = repo.list_days(clinic_id)
    if not days:
        raise DayNotFoundError(clinic_id, "any")
    return {
        **clinic_profile(clinic_id),
        "days": [
            {
                "business_date": d.business_date,
                "business_date_display": date.fromisoformat(d.business_date).strftime("%-d %b %Y"),
                "row_count": d.row_count,
                "ingested_at": d.ingested_at,
                "has_warnings": bool(d.warnings),
            }
            for d in days
        ],
    }


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------


def _top_n(limit: int) -> int:
    if limit < 1 or limit > MAX_TOP_N:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_limit",
                "message": f"limit must be between 1 and {MAX_TOP_N}, got {limit}",
            },
        )
    return limit


@router.get("/reports/{clinic_id}/{business_date}/reconciliation")
def get_reconciliation(clinic_id: str, business_date: str) -> dict[str, Any]:
    parsed, record = reports.day_context(clinic_id, business_date)
    return reports.build_reconciliation(parsed, record)


@router.get("/reports/{clinic_id}/{business_date}/analytics")
def get_analytics(clinic_id: str, business_date: str, limit: int = 5) -> dict[str, Any]:
    top_n = _top_n(limit)  # validate the argument before touching storage
    parsed, record = reports.day_context(clinic_id, business_date)
    return reports.build_analytics(parsed, record, top_n=top_n)


@router.get("/reports/{clinic_id}/{business_date}")
def get_full_report(clinic_id: str, business_date: str, limit: int = 5) -> dict[str, Any]:
    top_n = _top_n(limit)
    parsed, record = reports.day_context(clinic_id, business_date)
    return reports.build_full_report(parsed, record, top_n=top_n)


@router.delete("/reports/{clinic_id}/{business_date}", status_code=status.HTTP_204_NO_CONTENT)
def delete_day(clinic_id: str, business_date: str) -> Response:
    if not repo.delete_day(clinic_id, business_date):
        raise DayNotFoundError(clinic_id, business_date)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------
# Narrative
# --------------------------------------------------------------------------


def _narrative_payload(parsed, record, narrative: dict) -> dict[str, Any]:
    return {**reports.day_header(parsed, record), **narrative}


@router.post("/reports/{clinic_id}/{business_date}/narrative")
def create_narrative(
    clinic_id: str,
    business_date: str,
    force: bool = Query(False, description="Regenerate even if a current narrative is cached"),
) -> dict[str, Any]:
    """Generate the owner-facing summary for a day.

    Cached against the hash of the rows it describes, so re-ingesting a corrected
    day invalidates it automatically rather than leaving a stale summary attached
    to fresh numbers.
    """
    parsed, record = reports.day_context(clinic_id, business_date)

    if not force:
        cached = repo.load_narrative(clinic_id, business_date, record.payload_hash)
        if cached is not None:
            return _narrative_payload(parsed, record, {**cached, "cached": True})

    reconciliation, analytics = reports.computed(parsed)
    narrative = generate_narrative(
        reconciliation,
        analytics,
        clinic_id=record.clinic_id,
        business_date=parsed.business_date,
        clinic_name=clinic_profile(record.clinic_id)["name"],
    ).to_dict()

    repo.save_narrative(clinic_id, business_date, record.payload_hash, narrative)
    return _narrative_payload(parsed, record, {**narrative, "cached": False})


@router.get("/reports/{clinic_id}/{business_date}/narrative")
def get_cached_narrative(clinic_id: str, business_date: str) -> dict[str, Any]:
    """Return the cached narrative, or 404 asking the caller to generate one."""
    parsed, record = reports.day_context(clinic_id, business_date)
    cached = repo.load_narrative(clinic_id, business_date, record.payload_hash)

    if cached is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "narrative_not_current",
                "message": (
                    "No narrative has been generated for the current version of this day"
                ),
                "hint": (
                    f"POST {API_PREFIX}/reports/{clinic_id}/{business_date}/narrative "
                    "to generate one."
                ),
            },
        )
    return _narrative_payload(parsed, record, {**cached, "cached": True})


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------


@router.get("/health")
def health() -> dict[str, Any]:
    """Liveness, plus whether a narrative would come from the model or the fallback."""
    provider = default_provider()
    return {
        "status": "ok",
        "llm_configured": provider.is_configured,
        "llm_model": provider.model if provider.is_configured else None,
        "narrative_source_if_asked_now": "llm" if provider.is_configured else "fallback",
        "clinics": len(repo.list_clinics()),
    }
