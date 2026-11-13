"""One error format for the whole API: RFC 9457 "problem details" (docs/04 §7).

    {"type": "/problems/<code>", "title": ..., "status": ..., "code": ..., "detail": ...,
     "instance": "<path>", "request_id": ..., "errors": [...]}

Where errors come from, and what they become:
  * ProblemError raised by our code ............ exactly what the code says
  * Pydantic validation (bad request body) ....... 422 validation_error, one entry per field
  * Unknown route / wrong method ................. 404 not_found / 405 method_not_allowed
  * A database error:
      - 40P01 deadlock, 40001 serialization ...... 409 retry_conflict, retryable (03 §8). Never 500.
      - a named constraint we know ................ its mapped code (CONSTRAINT_CODES)
      - other integrity errors (SQLSTATE 23xxx) .. 409 conflict, no internals
      - 42501 insufficient privilege / RLS ........ 403 permission_denied (and logged: it means a bug)
  * Anything else ................................. 500 internal_error: request_id only, never SQL
                                                    or stack traces (docs/05 WEB-5)
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm.exc import StaleDataError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core import request_state

log = logging.getLogger("hms.errors")

PROBLEM_JSON = "application/problem+json"
RETRYABLE_SQLSTATES = {"40P01", "40001"}  # deadlock_detected, serialization_failure

# Named constraints -> (HTTP status, code, title). docs/04 §7 "constraint table".
CONSTRAINT_CODES: dict[str, tuple[int, str, str]] = {
    "bed_no_overlap": (409, "bed_already_allocated", "Bed already allocated"),
    "student_no_overlap": (409, "student_already_allocated", "Student already has a bed for these dates"),
    "leave_no_overlap": (409, "leave_overlaps_existing", "Leave overlaps an existing request"),
    "payments_reference_uq": (409, "duplicate_payment_reference", "This payment reference was already recorded"),
    "payment_submissions_utr_uq": (409, "utr_already_submitted", "This UTR was already submitted"),
    "ledger_guard_deposit_floor": (409, "insufficient_deposit", "Not enough deposit held"),
    "ledger_guard_refund_exceeds_credit": (409, "refund_exceeds_credit", "Refund exceeds the credit balance"),
    "ledger_guard_refund_not_approved": (409, "refund_not_approved", "Refund request is not approved"),
    "ledger_allocations_exceeds_balance": (409, "allocation_exceeds_balance", "Allocation exceeds the balance"),
    "ledger_allocations_guard": (409, "invalid_allocation", "Invalid allocation"),
    "ledger_waiver_fully_allocated": (409, "waiver_exceeds_outstanding", "Waiver must cover one charge exactly"),
    "complaint_categories_org_id_name_key": (409, "category_name_taken", "A category with this name exists"),
    "users_org_email_uq": (409, "email_in_use", "This email is already used in your organization"),
    "organizations_slug_key": (409, "slug_taken", "This organization address is taken"),
    # Phase 2: organization setup
    "academic_sessions_org_id_name_key": (409, "session_name_taken", "An academic session with this name exists"),
    "academic_terms_academic_session_id_name_key": (409, "term_name_taken", "A term with this name exists"),
    "academic_terms_academic_session_id_daterange_excl": (409, "term_overlaps", "Terms of one session can't overlap"),
    "hostels_org_id_code_key": (409, "hostel_code_taken", "A hostel with this code exists"),
    "hostels_org_id_name_key": (409, "hostel_name_taken", "A hostel with this name exists"),
    "buildings_org_id_hostel_id_name_key": (409, "building_name_taken", "A building with this name exists"),
    "floors_building_id_level_key": (409, "floor_exists", "This building already has that floor"),
    "room_types_org_id_hostel_id_name_key": (409, "room_type_name_taken", "A room type with this name exists"),
    "rooms_floor_id_number_key": (409, "room_number_taken", "This floor already has a room with that number"),
    "beds_room_id_label_key": (409, "bed_label_taken", "This room already has a bed with that label"),
    "staff_members_org_id_employee_code_key": (409, "employee_code_taken", "This Employee ID is in use"),
    "role_assignments_active_uq": (409, "role_already_granted", "This role is already granted"),
    "role_assignments_check1": (403, "self_grant_forbidden", "You can't grant yourself a role"),
    # Phase 5: money
    "refund_requests_second_person": (403, "second_person_required", "Another administrator must decide this refund"),
    # D74: two registration requests for one email at the same moment
    "student_requests_one_open_per_email": (409, "request_exists", "A request with this email is already open"),
    # D78: an Administrator email is unique across the platform (registration, or a changed email)
    # D80: a student email is unique across the platform (admission, an approved request, a changed email)
    "student_emails_pkey": (409, "student_email_taken", "This email already belongs to a student"),
    "users_student_email_required": (422, "validation_error", "A student needs an email address"),
    "administrator_emails_pkey": (
        409,
        "admin_email_taken",
        "This email already belongs to an organization's administrator",
    ),
    "role_assignments_one_administrator": (
        409,
        "one_administrator_only",
        "An organization has exactly one administrator",
    ),
    "payment_submissions_check3": (403, "second_person_required", "Another person must review this submission"),
    "payments_org_id_financial_year_receipt_seq_key": (409, "retry_conflict", "Temporary conflict, please retry"),
    "ledger_one_charge_per_period_uq": (409, "already_charged", "This fee was already charged for that period"),
    "ledger_one_refund_per_request_uq": (409, "refund_already_paid", "This refund was already paid"),
    "ledger_guard_reversal": (409, "invalid_reversal", "This entry can't be reversed that way"),
    "ledger_entries_reverses_entry_id_key": (409, "already_reversed", "This entry was already reversed"),
    "payment_reversals_pkey": (409, "already_reversed", "This payment was already reversed"),
}


class ProblemError(Exception):
    """Raise anywhere in request handling to return a specific problem response."""

    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        detail: str | None = None,
        *,
        errors: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{status} {code}")
        self.status, self.code, self.title, self.detail = status, code, title, detail
        self.errors, self.headers, self.extra = errors or [], headers or {}, extra or {}


def problem_body(
    status: int, code: str, title: str, path: str | None, detail: str | None = None, errors: Any = None, **extra: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": f"/problems/{code}",
        "title": title,
        "status": status,
        "code": code,
        "instance": path,
        "request_id": request_state.request_id.get(),
        "errors": errors or [],
    }
    if detail:
        body["detail"] = detail
    body.update(extra)
    return body


def problem_response(request: Request | None, status: int, code: str, title: str, **kw: Any) -> JSONResponse:
    headers = kw.pop("headers", None)
    path = request.url.path if request is not None else None
    return JSONResponse(
        problem_body(status, code, title, path, **kw), status_code=status, media_type=PROBLEM_JSON, headers=headers
    )


def db_error_response(request: Request | None, exc: DBAPIError) -> JSONResponse | None:
    """Map a database error to a response, or None if it is unexpected (-> 500)."""
    orig = exc.orig
    if not isinstance(orig, psycopg.Error):
        return None
    sqlstate = orig.sqlstate or ""
    constraint = orig.diag.constraint_name
    if sqlstate in RETRYABLE_SQLSTATES:
        return problem_response(
            request,
            409,
            "retry_conflict",
            "Temporary conflict, please retry",
            detail="Another change happened at the same moment. Nothing was saved; repeating the request is safe.",
            retryable=True,
            headers={"Retry-After": "1"},
        )
    if constraint in CONSTRAINT_CODES:
        status, code, title = CONSTRAINT_CODES[constraint]
        return problem_response(request, status, code, title)
    if sqlstate.startswith("23"):
        log.warning("unmapped integrity error", extra={"sqlstate": sqlstate, "constraint": constraint})
        return problem_response(request, 409, "conflict", "The request conflicts with the current data")
    if sqlstate == "42501":
        log.error("database refused an action (privilege or RLS)", extra={"sqlstate": sqlstate})
        return problem_response(request, 403, "permission_denied", "Not allowed")
    return None


def internal_error_response(request: Request | None) -> JSONResponse:
    return problem_response(
        request, 500, "internal_error", "Something went wrong", detail="Quote the request_id when reporting this."
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _problem(request: Request, exc: ProblemError) -> JSONResponse:
        return problem_response(
            request,
            exc.status,
            exc.code,
            exc.title,
            detail=exc.detail,
            errors=exc.errors,
            headers=exc.headers,
            **exc.extra,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                # ("body", "amount") -> "amount"; ("query", "page") -> "page"
                "field": ".".join(str(p) for p in err.get("loc", ())[1:]) or str(err.get("loc", [""])[0]),
                "code": err.get("type", "invalid"),
                "message": err.get("msg", "Invalid value"),
            }
            for err in exc.errors()
        ]
        return problem_response(request, 422, "validation_error", "Invalid request", errors=errors)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed", 401: "unauthorized"}.get(exc.status_code, "http_error")
        return problem_response(
            request,
            exc.status_code,
            code,
            str(exc.detail) if exc.status_code != 404 else "Not found",
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(StaleDataError)
    async def _stale(request: Request, exc: StaleDataError) -> JSONResponse:
        return problem_response(
            request,
            409,
            "edit_conflict",
            "Someone else changed this record",
            detail="It was updated after you opened it. Reload to see the latest version, then reapply your change.",
        )

    @app.exception_handler(DBAPIError)
    async def _db(request: Request, exc: DBAPIError) -> JSONResponse:
        response = db_error_response(request, exc)
        if response is None:
            log.exception("unexpected database error")
            return internal_error_response(request)
        return response
