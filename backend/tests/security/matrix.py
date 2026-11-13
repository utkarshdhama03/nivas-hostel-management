"""The route classification table (docs/05 §2): every API route is either in SECURITY_MATRIX
or in PUBLIC_ROUTES with a written reason. test_every_route_is_classified fails the build for
any route that is in neither, so a new endpoint can't skip the generated security suites.

SECURITY_MATRIX key: (METHOD, path template as FastAPI registers it).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from app.core import permissions as P_

Auth = Literal["org", "platform"]


@dataclass(frozen=True)
class RouteSpec:
    auth: Auth
    permission: str | None = None  # None = any signed-in user of that audience
    portal: Literal["staff", "student"] | None = None
    # path parameter -> resource type, used to substitute other organizations'/hostels' IDs
    params: dict[str, str] = field(default_factory=dict)
    # a VALID request body (so validation passes and the ID lookup decides the answer)
    body: Callable[[], dict[str, Any]] | None = None
    multipart: bool = False  # sends a CSV file instead of JSON


P = "/api/v1"


def staff(permission: str, **kw: Any) -> RouteSpec:
    return RouteSpec("org", permission=permission, portal="staff", **kw)


def v1() -> dict[str, Any]:
    return {"version": 1}


SECURITY_MATRIX: dict[tuple[str, str], RouteSpec] = {
    # --- me, notifications, sessions (Phase 1) ---
    ("GET", f"{P}/me"): RouteSpec("org"),
    ("GET", f"{P}/me/notifications"): RouteSpec("org"),
    ("GET", f"{P}/me/notifications/unread-count"): RouteSpec("org"),
    ("POST", f"{P}/me/notifications/{{notification_id}}/read"): RouteSpec(
        "org", params={"notification_id": "notification"}
    ),
    ("POST", f"{P}/me/notifications/read-all"): RouteSpec("org"),
    ("GET", f"{P}/me/notification-preferences"): RouteSpec("org"),
    ("PATCH", f"{P}/me/notification-preferences"): RouteSpec("org", body=lambda: {"preferences": []}),
    ("GET", f"{P}/auth/sessions"): RouteSpec("org"),
    ("POST", f"{P}/auth/sessions/{{session_id}}/revoke"): RouteSpec("org", params={"session_id": "session"}),
    ("POST", f"{P}/auth/sessions/revoke-others"): RouteSpec("org"),
    ("POST", f"{P}/auth/password/change"): RouteSpec(
        "org", body=lambda: {"current_password": "not-the-password", "new_password": "x" * 20}
    ),
    # --- student registration requests (D74) ---
    ("GET", f"{P}/admin/registration-requests"): staff(P_.STUDENTS_MANAGE),
    ("GET", f"{P}/admin/registration-requests/{{request_id}}"): staff(
        P_.STUDENTS_MANAGE, params={"request_id": "registration_request"}
    ),
    ("GET", f"{P}/admin/registration-requests/{{request_id}}/photo"): staff(
        P_.STUDENTS_MANAGE, params={"request_id": "registration_request"}
    ),
    ("POST", f"{P}/admin/registration-requests/{{request_id}}/approve"): staff(
        P_.STUDENTS_MANAGE, params={"request_id": "registration_request"}
    ),
    ("POST", f"{P}/admin/registration-requests/{{request_id}}/reject"): staff(
        P_.STUDENTS_MANAGE,
        params={"request_id": "registration_request"},
        body=lambda: {"reason": "Security test reason"},
    ),
    # --- organization and academic calendar (Phase 2) ---
    ("GET", f"{P}/admin/organization"): staff(P_.ORG_MANAGE),
    ("PATCH", f"{P}/admin/organization"): staff(P_.ORG_MANAGE, body=v1),
    ("GET", f"{P}/admin/academic-sessions"): staff(P_.ORG_MANAGE),
    ("POST", f"{P}/admin/academic-sessions"): staff(P_.ORG_MANAGE),
    ("PATCH", f"{P}/admin/academic-sessions/{{session_id}}"): staff(
        P_.ORG_MANAGE, params={"session_id": "academic_session"}, body=lambda: {"version": 1, "name": "Renamed"}
    ),
    ("POST", f"{P}/admin/academic-sessions/{{session_id}}/make-current"): staff(
        P_.ORG_MANAGE, params={"session_id": "academic_session"}
    ),
    ("GET", f"{P}/admin/academic-sessions/{{session_id}}/terms"): staff(
        P_.ORG_MANAGE, params={"session_id": "academic_session"}
    ),
    ("POST", f"{P}/admin/academic-sessions/{{session_id}}/terms"): staff(
        P_.ORG_MANAGE,
        params={"session_id": "academic_session"},
        body=lambda: {"name": "T9", "starts_on": "2026-07-01", "ends_on": "2026-08-01"},
    ),
    # --- hostel structure (Phase 2) ---
    ("GET", f"{P}/admin/hostels"): staff(P_.HOSTEL_VIEW),
    ("POST", f"{P}/admin/hostels"): staff(P_.HOSTEL_CREATE),
    ("GET", f"{P}/admin/hostels/{{hostel_id}}"): staff(P_.HOSTEL_VIEW, params={"hostel_id": "hostel"}),
    ("PATCH", f"{P}/admin/hostels/{{hostel_id}}"): staff(P_.HOSTEL_MANAGE, params={"hostel_id": "hostel"}, body=v1),
    ("GET", f"{P}/admin/hostels/{{hostel_id}}/buildings"): staff(P_.HOSTEL_VIEW, params={"hostel_id": "hostel"}),
    ("POST", f"{P}/admin/hostels/{{hostel_id}}/buildings"): staff(
        P_.HOSTEL_MANAGE, params={"hostel_id": "hostel"}, body=lambda: {"name": "Z"}
    ),
    ("GET", f"{P}/admin/buildings/{{building_id}}/floors"): staff(P_.HOSTEL_VIEW, params={"building_id": "building"}),
    ("POST", f"{P}/admin/buildings/{{building_id}}/floors"): staff(
        P_.HOSTEL_MANAGE, params={"building_id": "building"}, body=lambda: {"level": 9}
    ),
    ("GET", f"{P}/admin/hostels/{{hostel_id}}/room-types"): staff(P_.HOSTEL_VIEW, params={"hostel_id": "hostel"}),
    ("POST", f"{P}/admin/hostels/{{hostel_id}}/room-types"): staff(
        P_.HOSTEL_MANAGE, params={"hostel_id": "hostel"}, body=lambda: {"name": "Zz", "beds_per_room": 1}
    ),
    ("PATCH", f"{P}/admin/room-types/{{room_type_id}}"): staff(
        P_.HOSTEL_MANAGE, params={"room_type_id": "room_type"}, body=v1
    ),
    ("GET", f"{P}/admin/floors/{{floor_id}}/rooms"): staff(P_.HOSTEL_VIEW, params={"floor_id": "floor"}),
    ("POST", f"{P}/admin/floors/{{floor_id}}/rooms"): staff(
        P_.HOSTEL_MANAGE,
        params={"floor_id": "floor"},
        body=lambda: {"number": "999", "room_type_id": str(uuid.uuid4())},
    ),
    ("PATCH", f"{P}/admin/rooms/{{room_id}}"): staff(P_.HOSTEL_MANAGE, params={"room_id": "room"}, body=v1),
    ("GET", f"{P}/admin/rooms/{{room_id}}/beds"): staff(P_.HOSTEL_VIEW, params={"room_id": "room"}),
    ("POST", f"{P}/admin/rooms/{{room_id}}/beds"): staff(
        P_.HOSTEL_MANAGE, params={"room_id": "room"}, body=lambda: {"label": "Z"}
    ),
    ("PATCH", f"{P}/admin/beds/{{bed_id}}"): staff(P_.HOSTEL_MANAGE, params={"bed_id": "bed"}, body=v1),
    ("GET", f"{P}/admin/hostels/{{hostel_id}}/bed-board"): staff(P_.BED_BOARD_VIEW, params={"hostel_id": "hostel"}),
    ("POST", f"{P}/admin/hostels/{{hostel_id}}/imports/rooms-beds"): staff(
        P_.HOSTEL_MANAGE, params={"hostel_id": "hostel"}, multipart=True
    ),
    # --- the Administrator (Phase 2; one role since D75, one person since D76) ---
    ("GET", f"{P}/admin/staff"): staff(P_.STAFF_MANAGE),
    ("GET", f"{P}/admin/staff/{{staff_id}}"): staff(P_.STAFF_MANAGE, params={"staff_id": "staff"}),
    ("PATCH", f"{P}/admin/staff/{{staff_id}}"): staff(P_.STAFF_MANAGE, params={"staff_id": "staff"}, body=v1),
    ("POST", f"{P}/admin/users/{{user_id}}/activation-email"): staff(P_.STAFF_MANAGE, params={"user_id": "user"}),
    # --- students and files (Phase 3) ---
    ("PATCH", f"{P}/me"): RouteSpec("org"),
    ("GET", f"{P}/admin/students"): staff(P_.STUDENTS_VIEW),
    ("POST", f"{P}/admin/students"): staff(P_.STUDENTS_MANAGE),
    ("GET", f"{P}/admin/students/{{student_id}}"): staff(P_.STUDENTS_VIEW, params={"student_id": "student"}),
    ("PATCH", f"{P}/admin/students/{{student_id}}"): staff(
        P_.STUDENTS_MANAGE, params={"student_id": "student"}, body=v1
    ),
    ("POST", f"{P}/admin/students/{{student_id}}/exit"): staff(
        P_.STUDENTS_MANAGE,
        params={"student_id": "student"},
        body=lambda: {"exited_on": "2026-11-01", "exit_reason": "other"},
    ),
    ("GET", f"{P}/admin/students/{{student_id}}/guardians"): staff(
        P_.STUDENT_CONTACTS_VIEW, params={"student_id": "student"}
    ),
    ("POST", f"{P}/admin/students/{{student_id}}/guardians"): staff(
        P_.STUDENTS_MANAGE,
        params={"student_id": "student"},
        body=lambda: {"full_name": "Z", "relationship": "other", "phone": "+91 90000 00000"},
    ),
    ("PATCH", f"{P}/admin/students/{{student_id}}/guardians/{{guardian_id}}"): staff(
        P_.STUDENTS_MANAGE, params={"student_id": "student", "guardian_id": "guardian"}, body=v1
    ),
    ("GET", f"{P}/admin/students/{{student_id}}/documents"): staff(
        P_.STUDENTS_MANAGE, params={"student_id": "student"}
    ),
    ("POST", f"{P}/admin/students/{{student_id}}/documents"): staff(
        P_.STUDENTS_MANAGE,
        params={"student_id": "student"},
        body=lambda: {"file_id": str(uuid.uuid4()), "document_type": "admission"},
    ),
    ("GET", f"{P}/admin/students/{{student_id}}/documents/{{document_id}}/content"): staff(
        P_.STUDENT_DOCUMENTS, params={"student_id": "student", "document_id": "document"}
    ),
    ("POST", f"{P}/admin/students/{{student_id}}/documents/{{document_id}}/verify"): staff(
        P_.STUDENTS_MANAGE, params={"student_id": "student", "document_id": "document"}
    ),
    ("POST", f"{P}/files"): RouteSpec("org", multipart=True),
    ("GET", f"{P}/files/{{file_id}}/link"): RouteSpec("org", params={"file_id": "file"}),
    ("GET", f"{P}/portal/me/profile"): RouteSpec("org", portal="student"),
    ("POST", f"{P}/portal/me/emergency-contact"): RouteSpec("org", portal="student"),
    # --- bed allocation (Phase 4) ---
    ("GET", f"{P}/admin/allocations"): staff(P_.STUDENTS_VIEW),
    ("POST", f"{P}/admin/allocations"): staff(P_.ALLOCATIONS_MANAGE),
    ("POST", f"{P}/admin/allocations/{{allocation_id}}/check-in"): staff(
        P_.ALLOCATIONS_MANAGE, params={"allocation_id": "allocation"}
    ),
    ("POST", f"{P}/admin/allocations/{{allocation_id}}/check-out"): staff(
        P_.ALLOCATIONS_MANAGE, params={"allocation_id": "allocation"}
    ),
    ("POST", f"{P}/admin/allocations/{{allocation_id}}/cancel"): staff(
        P_.ALLOCATIONS_MANAGE, params={"allocation_id": "allocation"}
    ),
    ("POST", f"{P}/admin/allocations/{{allocation_id}}/transfer"): staff(
        P_.ALLOCATIONS_MANAGE, params={"allocation_id": "allocation"}, body=lambda: {"bed_id": str(uuid.uuid4())}
    ),
    ("GET", f"{P}/admin/students/{{student_id}}/allocations"): staff(
        P_.STUDENTS_VIEW, params={"student_id": "student"}
    ),
    ("GET", f"{P}/admin/beds/{{bed_id}}/allocations"): staff(P_.HOSTEL_VIEW, params={"bed_id": "bed"}),
    # --- fees, payments and the ledger (Phase 5) ---
    ("GET", f"{P}/admin/fee-structures"): staff(P_.FINANCE_VIEW),
    ("POST", f"{P}/admin/fee-structures"): staff(P_.FINANCE_MANAGE),
    ("PATCH", f"{P}/admin/fee-structures/{{fee_structure_id}}"): staff(
        P_.FINANCE_MANAGE, params={"fee_structure_id": "fee_structure"}, body=v1
    ),
    ("POST", f"{P}/admin/charge-runs"): staff(P_.FINANCE_MANAGE),
    ("GET", f"{P}/admin/charge-runs/{{job_id}}"): staff(P_.FINANCE_MANAGE, params={"job_id": "job"}),
    ("GET", f"{P}/admin/students/{{student_id}}/balance"): staff(P_.FINANCE_VIEW, params={"student_id": "student"}),
    ("GET", f"{P}/admin/students/{{student_id}}/ledger"): staff(P_.FINANCE_VIEW, params={"student_id": "student"}),
    ("POST", f"{P}/admin/students/{{student_id}}/charges"): staff(
        P_.FINANCE_MANAGE,
        params={"student_id": "student"},
        body=lambda: {"kind": "charge", "amount": "10.00", "description": "Test", "due_date": "2026-11-01"},
    ),
    ("POST", f"{P}/admin/students/{{student_id}}/waivers"): staff(
        P_.FINANCE_MANAGE,
        params={"student_id": "student"},
        body=lambda: {
            "target_entry_id": str(uuid.uuid4()),
            "amount": "1.00",
            "reason_code": "waiver",
            "reason_note": "Test",
        },
    ),
    ("POST", f"{P}/admin/ledger-entries/{{entry_id}}/reverse"): staff(
        P_.FINANCE_MANAGE, params={"entry_id": "ledger_entry"}, body=lambda: {"reason": "security test"}
    ),
    ("POST", f"{P}/admin/students/{{student_id}}/deposit/apply"): staff(
        P_.FINANCE_MANAGE, params={"student_id": "student"}, body=lambda: {"amount": "1.00"}
    ),
    ("POST", f"{P}/admin/payments"): staff(P_.FINANCE_MANAGE),
    ("GET", f"{P}/admin/payments"): staff(P_.FINANCE_VIEW),
    ("GET", f"{P}/admin/payments/export.csv"): staff(P_.FINANCE_VIEW),
    ("GET", f"{P}/admin/payments/{{payment_id}}"): staff(P_.FINANCE_VIEW, params={"payment_id": "payment"}),
    ("GET", f"{P}/admin/payments/{{payment_id}}/receipt.pdf"): staff(P_.FINANCE_VIEW, params={"payment_id": "payment"}),
    ("POST", f"{P}/admin/payments/{{payment_id}}/reverse"): staff(
        P_.FINANCE_MANAGE, params={"payment_id": "payment"}, body=lambda: {"reason": "security test"}
    ),
    ("GET", f"{P}/admin/payment-submissions"): staff(P_.FINANCE_MANAGE),
    ("POST", f"{P}/admin/payment-submissions/{{submission_id}}/verify"): staff(
        P_.FINANCE_MANAGE, params={"submission_id": "submission"}
    ),
    ("POST", f"{P}/admin/payment-submissions/{{submission_id}}/reject"): staff(
        P_.FINANCE_MANAGE, params={"submission_id": "submission"}, body=lambda: {"note": "security test"}
    ),
    ("GET", f"{P}/admin/refund-requests"): staff(P_.FINANCE_VIEW),
    ("POST", f"{P}/admin/refund-requests"): staff(P_.FINANCE_MANAGE),
    ("POST", f"{P}/admin/refund-requests/{{refund_id}}/approve"): staff(
        P_.REFUNDS_APPROVE, params={"refund_id": "refund"}
    ),
    ("POST", f"{P}/admin/refund-requests/{{refund_id}}/reject"): staff(
        P_.REFUNDS_APPROVE, params={"refund_id": "refund"}, body=lambda: {"note": "security test"}
    ),
    ("POST", f"{P}/admin/refund-requests/{{refund_id}}/cancel"): staff(
        P_.FINANCE_MANAGE, params={"refund_id": "refund"}
    ),
    ("POST", f"{P}/admin/refund-requests/{{refund_id}}/mark-paid"): staff(
        P_.FINANCE_MANAGE, params={"refund_id": "refund"}, body=lambda: {"method": "cash"}
    ),
    ("GET", f"{P}/portal/me/fees"): RouteSpec("org", portal="student"),
    ("POST", f"{P}/portal/me/payment-submissions"): RouteSpec("org", portal="student"),
    ("GET", f"{P}/portal/me/payments/{{payment_id}}/receipt.pdf"): RouteSpec(
        "org", portal="student", params={"payment_id": "payment"}
    ),
    # --- leave (Phase 6; no gate since D75) ---
    ("GET", f"{P}/admin/leave-requests"): staff(P_.LEAVE_MANAGE),
    ("POST", f"{P}/admin/leave-requests"): staff(P_.LEAVE_MANAGE),
    ("POST", f"{P}/admin/leave-requests/{{leave_id}}/approve"): staff(
        P_.LEAVE_MANAGE, params={"leave_id": "leave"}, body=lambda: {}
    ),
    ("POST", f"{P}/admin/leave-requests/{{leave_id}}/reject"): staff(
        P_.LEAVE_MANAGE, params={"leave_id": "leave"}, body=lambda: {"note": "security test"}
    ),
    ("GET", f"{P}/portal/me/leave-requests"): RouteSpec("org", portal="student"),
    ("POST", f"{P}/portal/me/leave-requests"): RouteSpec("org", portal="student"),
    ("POST", f"{P}/portal/me/leave-requests/{{leave_id}}/cancel"): RouteSpec(
        "org", portal="student", params={"leave_id": "leave"}
    ),
    ("GET", f"{P}/admin/audit-logs"): staff(P_.AUDIT_READ),
    # --- complaints, mess, announcements (Phase 8) ---
    ("GET", f"{P}/admin/complaint-categories"): RouteSpec("org", portal="staff"),
    ("POST", f"{P}/admin/complaint-categories"): staff(P_.ORG_MANAGE),
    ("PATCH", f"{P}/admin/complaint-categories/{{category_id}}"): staff(
        P_.ORG_MANAGE, params={"category_id": "complaint_category"}, body=v1
    ),
    ("GET", f"{P}/admin/complaints"): staff(P_.COMPLAINTS_MANAGE),
    ("GET", f"{P}/admin/complaints/{{complaint_id}}"): staff(
        P_.COMPLAINTS_MANAGE, params={"complaint_id": "complaint"}
    ),
    ("POST", f"{P}/admin/complaints/{{complaint_id}}/assign"): staff(
        P_.COMPLAINTS_MANAGE, params={"complaint_id": "complaint"}, body=lambda: {"user_id": str(uuid.uuid4())}
    ),
    ("POST", f"{P}/admin/complaints/{{complaint_id}}/start"): staff(
        P_.COMPLAINTS_MANAGE, params={"complaint_id": "complaint"}
    ),
    ("POST", f"{P}/admin/complaints/{{complaint_id}}/resolve"): staff(
        P_.COMPLAINTS_MANAGE, params={"complaint_id": "complaint"}, body=lambda: {"note": "security test"}
    ),
    ("POST", f"{P}/admin/complaints/{{complaint_id}}/comments"): staff(
        P_.COMPLAINTS_MANAGE, params={"complaint_id": "complaint"}, body=lambda: {"comment": "security test"}
    ),
    ("GET", f"{P}/portal/me/complaints"): RouteSpec("org", portal="student"),
    ("GET", f"{P}/portal/me/complaint-categories"): RouteSpec("org", portal="student"),
    ("POST", f"{P}/portal/me/complaints"): RouteSpec("org", portal="student"),
    ("POST", f"{P}/portal/me/complaints/{{complaint_id}}/reopen"): RouteSpec(
        "org", portal="student", params={"complaint_id": "complaint"}, body=lambda: {"comment": "security test"}
    ),
    ("GET", f"{P}/admin/hostels/{{hostel_id}}/mess-menu"): staff(P_.MESS_MANAGE, params={"hostel_id": "hostel"}),
    ("POST", f"{P}/admin/hostels/{{hostel_id}}/mess-menu/entries"): staff(
        P_.MESS_MANAGE,
        params={"hostel_id": "hostel"},
        body=lambda: {"entries": [{"menu_date": "2026-11-09", "meal": "lunch", "items": "Rice"}]},
    ),
    ("POST", f"{P}/admin/hostels/{{hostel_id}}/mess-menu/copy"): staff(
        P_.MESS_MANAGE,
        params={"hostel_id": "hostel"},
        body=lambda: {"from_week_start": "2026-11-02", "to_week_start": "2026-11-09"},
    ),
    ("GET", f"{P}/portal/me/mess-menu"): RouteSpec("org", portal="student"),
    ("GET", f"{P}/admin/announcements"): staff(P_.ANNOUNCEMENTS_MANAGE),
    ("POST", f"{P}/admin/announcements"): staff(P_.ANNOUNCEMENTS_MANAGE),
    ("GET", f"{P}/portal/me/announcements"): RouteSpec("org", portal="student"),
    # --- the home overview (D86; analytics and its reports were removed) ---
    ("GET", f"{P}/admin/overview"): staff(P_.STUDENTS_VIEW),
    # --- platform: cross-tenant by design; org tokens must never get in ---
    ("GET", f"{P}/platform/organizations"): RouteSpec("platform"),
    ("GET", f"{P}/platform/organizations/{{org_id}}"): RouteSpec("platform", params={"org_id": "organization"}),
    ("POST", f"{P}/platform/organizations/{{org_id}}/suspend"): RouteSpec(
        "platform", params={"org_id": "organization"}, body=lambda: {"reason": "security test reason"}
    ),
    ("POST", f"{P}/platform/organizations/{{org_id}}/reactivate"): RouteSpec(
        "platform", params={"org_id": "organization"}, body=lambda: {"reason": "security test reason"}
    ),
    ("GET", f"{P}/platform/health"): RouteSpec("platform"),
    ("GET", f"{P}/platform/audit-logs"): RouteSpec("platform"),
    ("GET", f"{P}/platform/settings"): RouteSpec("platform"),
    ("PATCH", f"{P}/platform/settings"): RouteSpec(
        "platform", body=lambda: {"key": "health.disk_warn_percent", "value": 80}
    ),
    ("GET", f"{P}/platform/me"): RouteSpec("platform"),
}

PUBLIC_ROUTES: dict[tuple[str, str], str] = {
    ("GET", f"{P}/health/live"): "liveness probe; touches nothing",
    ("GET", f"{P}/files/{{file_id}}/content"): "signed 60-second link bound to user + session is the credential",
    ("GET", f"{P}/health/ready"): "loopback only; reveals only the schema version",
    ("GET", f"{P}/public/organizations/{{org_slug}}"): "login page needs the organization name; slugs aren't secret",
    ("GET", f"{P}/public/slug-availability"): "registration form; rate-limited",
    ("POST", f"{P}/public/organizations"): "self-registration (04 §5.2); rate-limited",
    ("POST", f"{P}/o/{{org_slug}}/auth/verify-registration"): "one-time emailed token is the credential",
    ("POST", f"{P}/o/{{org_slug}}/auth/login"): "the password is the credential; lockout + IP throttle",
    ("POST", f"{P}/o/{{org_slug}}/auth/activate/preview"): "one-time activation token is the credential",
    ("POST", f"{P}/o/{{org_slug}}/auth/activate"): "one-time activation token is the credential",
    ("POST", f"{P}/o/{{org_slug}}/auth/password-reset/request"): "always 202; rate-limited",
    ("POST", f"{P}/o/{{org_slug}}/auth/password-reset/confirm"): (
        "emailed 6-digit code (D77): 10 minutes, single use, 5 tries per code, 20 wrong per IP"
    ),
    ("GET", f"{P}/public/organizations/{{org_slug}}/student-registration"): (
        "the registration form's facts (D74); an unknown organization looks like a closed one"
    ),
    ("POST", f"{P}/public/organizations/{{org_slug}}/student-registration"): (
        "the registration form (D74): 5/h per IP, 50/day per organization, honeypot, always 202"
    ),
    ("POST", f"{P}/o/{{org_slug}}/auth/student-registration/verify"): "one-time emailed token (D74)",
    ("POST", f"{P}/auth/login"): "D78: Administrator email + password; same lockout and IP throttle",
    ("POST", f"{P}/auth/password-reset/request"): "D78: always 202; rate-limited (as the org route)",
    ("POST", f"{P}/auth/password-reset/confirm"): "D78: emailed 6-digit code, as the org route",
    ("POST", f"{P}/auth/refresh"): "refresh cookie + CSRF header + Origin are the credential",
    ("POST", f"{P}/auth/logout"): "refresh cookie + CSRF header + Origin; idempotent",
    ("POST", f"{P}/platform/auth/login"): "platform email + password (D87: no second step); account lock",
    ("POST", f"{P}/platform/auth/refresh"): "platform refresh cookie + CSRF",
    ("POST", f"{P}/platform/auth/logout"): "platform refresh cookie + CSRF; idempotent",
}
