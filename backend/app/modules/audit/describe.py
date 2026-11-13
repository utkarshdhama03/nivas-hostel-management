"""The audit log in plain language (D89). Presentation only: rows are read, never changed.

The stored rows keep only allowlisted values (app/core/audit.py), so most of them hold no amount and
no name. To say "Kavita Deshpande approved a refund of ₹2,500 for Saurabh Parikh", this module looks
the details up in the record each row points to (the refund request, its student...) as that record
is NOW. A name corrected later shows the corrected name (accepted, D89). A record that no longer exists
(a purged registration request) simply gives a shorter sentence and no link.

Everything runs under the caller's RLS context, like any other query: only this organization's rows.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, and_, case, column, exists, func, or_, select, table
from sqlalchemy.orm import Session

from app.core.models import AuditLog

# --- Categories ---------------------------------------------------------------------------------------

CATEGORIES = (
    "payments",
    "refunds",
    "students",
    "registration",
    "rooms",
    "leave",
    "complaints",
    "notices",
    "settings",
    "signin",
    "other",
)

# Whole action codes that don't follow their prefix's category.
_EXACT: dict[str, str] = {
    "account.activated": "signin",
    "activation_email.sent": "signin",
    "user.password_changed": "signin",
    "user.password_reset": "signin",
    "session.reuse_detected": "signin",
    "user.updated": "settings",
    "file.uploaded": "students",
}
# The part of the action code before the dot.
_PREFIX: dict[str, str] = {
    "payment": "payments",
    "payment_submission": "payments",
    "payments": "payments",
    "ledger": "payments",
    "fee_structure": "payments",
    "charge_run": "payments",
    "deposit": "payments",
    "refund": "refunds",
    "student": "students",
    "student_document": "students",
    "student_request": "registration",
    "allocation": "rooms",
    "hostel": "rooms",
    "room": "rooms",
    "room_type": "rooms",
    "bed": "rooms",
    "leave": "leave",
    "complaint": "complaints",
    "complaint_category": "complaints",
    "mess_menu": "notices",
    "announcement": "notices",
    "organization": "settings",
    "academic_session": "settings",
    "academic_term": "settings",
    "notification_preference": "settings",
    "staff": "settings",
    "platform": "settings",
    "platform_user": "settings",
    "health": "settings",
}


def category_of(action: str) -> str:
    if action in _EXACT:
        return _EXACT[action]
    return _PREFIX.get(action.split(".", 1)[0], "other")


def category_condition(category: str) -> ColumnElement[bool]:
    """SQL for "this row's category is `category`", the same rule as category_of()."""
    prefix = func.split_part(AuditLog.action, ".", 1)
    if category == "other":
        return and_(AuditLog.action.not_in(list(_EXACT)), prefix.not_in(list(_PREFIX)))
    exact_in = [a for a, c in _EXACT.items() if c == category]
    exact_out = [a for a, c in _EXACT.items() if c != category]
    prefixes = [p for p, c in _PREFIX.items() if c == category]
    return or_(AuditLog.action.in_(exact_in), and_(prefix.in_(prefixes), AuditLog.action.not_in(exact_out)))


# --- Which student a row is about -----------------------------------------------------------------------


def _t(name: str, *cols: str) -> Any:
    return table(name, *(column(c) for c in cols))


_STUDENTS = _t("students", "id", "user_id", "student_code", "hostel_id")
_USERS = _t("users", "id", "full_name", "kind")
_REQUESTS = _t("student_registration_requests", "id", "student_id", "full_name")
# entity_type -> table whose row carries student_id
_STUDENT_OF: dict[str, str] = {
    "payment": "payments",
    "payment_submission": "payment_submissions",
    "refund_request": "refund_requests",
    "ledger_entry": "ledger_entries",
    "bed_allocation": "bed_allocations",
    "leave_request": "leave_requests",
    "student_request": "student_registration_requests",
}


def _subject_student_sql() -> Any:
    whens: list[tuple[Any, Any]] = [(AuditLog.entity_type == "student", AuditLog.entity_id)]
    for entity, name in _STUDENT_OF.items():
        t = _t(name, "id", "student_id")
        whens.append(
            (
                AuditLog.entity_type == entity,
                select(t.c.student_id).where(t.c.id == AuditLog.entity_id).correlate(AuditLog).scalar_subquery(),
            )
        )
    whens.append(
        (
            AuditLog.entity_type == "user",
            select(_STUDENTS.c.id)
            .where(_STUDENTS.c.user_id == AuditLog.entity_id)
            .correlate(AuditLog)
            .scalar_subquery(),
        )
    )
    return case(*whens, else_=None)


def escape_like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def search_condition(q: str) -> ColumnElement[bool]:
    """Who did it, or the student (or applicant, or person) it is about, by name or Student ID."""
    like = escape_like(q)
    subject = _subject_student_sql()
    return or_(
        exists(
            select(1).select_from(_USERS).where(_USERS.c.id == AuditLog.actor_user_id, _USERS.c.full_name.ilike(like))
        ),
        exists(
            select(1)
            .select_from(_STUDENTS.join(_USERS, _USERS.c.id == _STUDENTS.c.user_id))
            .where(_STUDENTS.c.id == subject, or_(_USERS.c.full_name.ilike(like), _STUDENTS.c.student_code.ilike(like)))
        ),
        and_(
            AuditLog.entity_type == "student_request",
            exists(
                select(1)
                .select_from(_REQUESTS)
                .where(_REQUESTS.c.id == AuditLog.entity_id, _REQUESTS.c.full_name.ilike(like))
            ),
        ),
        and_(
            AuditLog.entity_type == "user",
            exists(
                select(1).select_from(_USERS).where(_USERS.c.id == AuditLog.entity_id, _USERS.c.full_name.ilike(like))
            ),
        ),
    )


def actor_condition(actor: str) -> ColumnElement[bool]:
    """'system', 'students', or one user's ID."""
    if actor == "system":
        return AuditLog.actor_type == "system"
    if actor == "students":
        return exists(
            select(1).select_from(_USERS).where(_USERS.c.id == AuditLog.actor_user_id, _USERS.c.kind == "student")
        )
    return AuditLog.actor_user_id == uuid.UUID(actor)


# --- Formatting --------------------------------------------------------------------------------------------


def rupees(value: Any) -> str | None:
    """₹ with Indian digit grouping: 250000 -> ₹2,50,000; paise only when there are some."""
    try:
        d = abs(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return None
    whole = int(d)
    paise = int((d - whole) * 100)
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return f"₹{s}" + (f".{paise:02d}" if paise else "")


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def day(iso: str) -> str:
    y, m, d = iso[:10].split("-")
    return f"{d}-{m}-{y}"


def when(iso: str, zone: ZoneInfo) -> str:
    t = datetime.fromisoformat(iso).astimezone(zone)
    return f"{t:%d-%m-%Y}, {t.hour % 12 or 12}:{t:%M} {'AM' if t.hour < 12 else 'PM'}"


LABELS: dict[str, str] = {
    "name": "Name",
    "code": "Code",
    "slug": "Sign-in name",
    "status": "Status",
    "timezone": "Time zone",
    "suspension_reason": "Suspension reason",
    "legal_name": "Legal name",
    "contact_email": "Contact email",
    "contact_phone": "Contact phone",
    "address_line1": "Address",
    "address_line2": "Address (line 2)",
    "address": "Address",
    "city": "City",
    "state": "State",
    "postal_code": "PIN code",
    "upi_id": "UPI ID",
    "upi_payee_name": "UPI payee name",
    "accepts_student_requests": "Accepts registration requests",
    "kind": "Account type",
    "full_name": "Name",
    "email": "Email",
    "phone": "Phone",
    "read_only_until": "Read-only until",
    "locked_until": "Locked until",
    "employee_code": "Employee ID",
    "designation": "Designation",
    "joined_on": "Joined on",
    "left_on": "Left on",
    "delivery": "Link sent by",
    "reactivation_reason": "Reason",
    "role": "Role",
    "revoked_at": "Ended at",
    "revoke_reason": "Reason",
    "category": "Category",
    "email_enabled": "Email on",
    "starts_on": "Starts on",
    "ends_on": "Ends on",
    "is_current": "Current year",
    "resident_type": "Residents",
    "local_outing_requires_approval": "Local outings need approval",
    "is_active": "In use",
    "buildings": "Buildings",
    "floors": "Floors",
    "rooms": "Rooms",
    "beds": "Beds",
    "description": "Description",
    "beds_per_room": "Beds per room",
    "notes": "Notes",
    "room_type_id": "Room type",
    "reject_reason": "Reason for rejecting",
    "student_code": "Student ID",
    "self_approved": "Self-approved",
    "entries": "Menu entries",
    "from_hostel_id": "Copied from hostel",
    "from_week_start": "From the week of",
    "to_week_start": "To the week of",
    "copied": "Entries copied",
    "title": "Title",
    "audience": "Audience",
    "exited_on": "Exit date",
    "exit_reason": "Exit reason",
    "outstanding_dues": "Dues outstanding",
    "deposit_held": "Deposit held",
    "outstanding_note": "Note",
    "amount": "Amount",
    "reason": "Reason",
    "method": "Method",
    "bed_id": "Bed",
    "previous_allocation_id": "Previous allocation",
    "period_start": "Period from",
    "key": "Setting",
    "value": "Value",
}
_MONEY_FIELDS = frozenset({"amount", "outstanding_dues", "deposit_held", "late_fee_amount", "late_fee_cap"})
WORDS: dict[str, str] = {
    "available": "in use",
    "maintenance": "under maintenance",
    "inactive": "not in use",
    "pending_verification": "waiting for email verification",
    "active": "active",
    "read_only": "read-only",
    "super_admin": "Administrator",
    "everyone": "everyone",
    "girls": "girls",
    "boys": "boys",
    "co_ed": "co-ed",
    "email": "email",
    "copied_link": "copied link",
    "upi": "UPI",
    "cash": "cash",
    "bank_transfer": "bank transfer",
    "home": "home",
    "local_outing": "local outing",
    "emergency": "emergency",
    "identity_document": "identity document",
    "student_photo": "student photo",
}


LEAVE = {"home": "home leave", "local_outing": "a local outing", "emergency": "emergency leave"}
_ORDER = {name: i for i, name in enumerate(LABELS)}


def label(field_name: str) -> str:
    return LABELS.get(field_name) or field_name.replace("_", " ").capitalize()


def words(value: str) -> str:
    return WORDS.get(value, value.replace("_", " "))


# --- What one row needs from other tables ---------------------------------------------------------------------


@dataclass
class Lookups:
    users: dict[uuid.UUID, tuple[str, str]] = field(default_factory=dict)  # id -> (full_name, kind)
    students: dict[uuid.UUID, tuple[str, str, uuid.UUID]] = field(default_factory=dict)  # id -> (name, code, user_id)
    student_by_user: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict)
    rows: dict[str, dict[uuid.UUID, dict[str, Any]]] = field(default_factory=dict)  # entity table -> id -> row
    names: dict[uuid.UUID, str] = field(default_factory=dict)  # hostels, room types, fee structures... by ID


_DETAIL_TABLES: dict[str, tuple[str, tuple[str, ...]]] = {
    "payment": ("payments", ("id", "student_id", "amount", "method", "receipt_number")),
    "payment_submission": ("payment_submissions", ("id", "student_id", "amount")),
    "refund_request": ("refund_requests", ("id", "student_id", "amount", "paid_method")),
    "ledger_entry": ("ledger_entries", ("id", "student_id", "amount", "entry_type")),
    "bed_allocation": ("bed_allocations", ("id", "student_id", "bed_id")),
    "leave_request": ("leave_requests", ("id", "student_id", "leave_type", "starts_at", "ends_at")),
    "student_request": ("student_registration_requests", ("id", "student_id", "full_name")),
    "bed": ("beds", ("id", "label", "room_id", "hostel_id")),
    "room": ("rooms", ("id", "number", "hostel_id")),
    "file": ("files", ("id", "category")),
    "announcement": ("announcements", ("id", "title")),
}
# entity_type -> table with a "name" column
_NAMED: dict[str, str] = {
    "hostel": "hostels",
    "mess_menu": "hostels",  # mess_menu rows point at their hostel
    "fee_structure": "fee_structures",
    "room_type": "room_types",
    "complaint_category": "complaint_categories",
    "academic_session": "academic_sessions",
    "academic_term": "academic_terms",
    "organization": "organizations",
}


def _load(db: Session, name: str, cols: tuple[str, ...], ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
    ids = list({i for i in ids if i})
    if not ids:
        return {}
    t = _t(name, *cols)
    return {r.id: dict(r._mapping) for r in db.execute(select(t).where(t.c.id.in_(ids)))}


def _uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str) and _UUID.match(value):
        return uuid.UUID(value)
    return None


def load(db: Session, logs: list[AuditLog]) -> Lookups:
    lk = Lookups()
    by_type: dict[str, set[uuid.UUID]] = defaultdict(set)
    for r in logs:
        if r.entity_id:
            by_type[r.entity_type].add(r.entity_id)
    for entity, (name, cols) in _DETAIL_TABLES.items():
        lk.rows[entity] = _load(db, name, cols, by_type.get(entity, ()))
    # beds reached through allocations, and their rooms
    bed_ids = {row["bed_id"] for row in lk.rows["bed_allocation"].values()}
    for r in logs:
        for values in (r.before_values or {}, r.after_values or {}):
            if r.entity_type == "bed_allocation" and _uuid(values.get("bed_id")):
                bed_ids.add(_uuid(values.get("bed_id")))
    lk.rows["bed"] |= _load(db, "beds", _DETAIL_TABLES["bed"][1], bed_ids - set(lk.rows["bed"]))
    room_ids = {row["room_id"] for row in lk.rows["bed"].values()}
    lk.rows["room"] |= _load(db, "rooms", _DETAIL_TABLES["room"][1], room_ids - set(lk.rows["room"]))

    # names of hostels, room types, fee structures... (the rows' own hostel too, and IDs inside values)
    named_ids: dict[str, set[uuid.UUID]] = defaultdict(set)
    for entity, name in _NAMED.items():
        named_ids[name] |= by_type.get(entity, set())
    for r in logs:
        if r.hostel_id:
            named_ids["hostels"].add(r.hostel_id)
        for values in (r.before_values or {}, r.after_values or {}):
            if _uuid(values.get("from_hostel_id")):
                named_ids["hostels"].add(_uuid(values["from_hostel_id"]))  # type: ignore[arg-type]
            if _uuid(values.get("room_type_id")):
                named_ids["room_types"].add(_uuid(values["room_type_id"]))  # type: ignore[arg-type]
    for name, ids in named_ids.items():
        for i, row in _load(db, name, ("id", "name"), ids).items():
            lk.names[i] = row["name"]

    # people: actors, user entities, students
    user_ids = {r.actor_user_id for r in logs if r.actor_user_id} | by_type.get("user", set())
    for i, row in _load(db, "users", ("id", "full_name", "kind"), user_ids).items():
        lk.users[i] = (row["full_name"], row["kind"])
    student_ids = set(by_type.get("student", set()))
    for rows in lk.rows.values():
        student_ids |= {row["student_id"] for row in rows.values() if row.get("student_id")}
    q = (
        select(_STUDENTS.c.id, _STUDENTS.c.student_code, _STUDENTS.c.user_id, _USERS.c.full_name)
        .select_from(_STUDENTS.join(_USERS, _USERS.c.id == _STUDENTS.c.user_id))
        .where(
            or_(
                _STUDENTS.c.id.in_(student_ids or [uuid.UUID(int=0)]),
                _STUDENTS.c.user_id.in_(user_ids or [uuid.UUID(int=0)]),
            )
        )
    )
    for s in db.execute(q):
        lk.students[s.id] = (s.full_name, s.student_code, s.user_id)
        lk.student_by_user[s.user_id] = s.id
    return lk


# --- Sentences ----------------------------------------------------------------------------------------------------

# {key} uses the key or its default; [ ... ] is dropped when any key inside it is unknown.
SENTENCES: dict[str, str] = {
    "academic_session.created": "{actor} created the academic year[ {name}]",
    "academic_session.updated": "{actor} changed the academic year[ {name}]",
    "academic_session.made_current": "{actor} made[ {name}] the current academic year",
    "academic_term.created": "{actor} added the term[ {name}]",
    "account.activated": "{person} activated their account",
    "activation_email.sent": "{actor} sent an activation email[ to {person}]",
    "allocation.created": "{actor} allocated {bed} to {student}",
    "allocation.reserved": "{actor} reserved {bed} for {student}",
    "allocation.checked_in": "{actor} checked {student} in to {bed}",
    "allocation.checked_out": "{actor} checked {student} out of {bed}",
    "allocation.cancelled": "{actor} cancelled the reservation of {bed} for {student}",
    "allocation.transferred": "{actor} moved {student} to {bed}",
    "allocation.reservation_expired": "The reservation of {bed} for {student} expired",
    "announcement.published": "{actor} published the announcement[ “{title}”]",
    "bed.updated": "{actor} marked {bed}[ as {status}]",
    "charge_run.requested": "{actor} generated charges[ for “{name}”]",
    "complaint_category.created": "{actor} added the complaint category[ “{name}”]",
    "complaint_category.updated": "{actor} changed the complaint category[ “{name}”]",
    "deposit.applied": "{actor} used {student_s} deposit to pay dues",
    "fee_structure.created": "{actor} created the fee[ “{name}”]",
    "fee_structure.updated": "{actor} changed the fee[ “{name}”]",
    "file.uploaded": "{actor} uploaded a file[ ({file})]",
    "hostel.created": "{actor} created the hostel[ {name}]",
    "hostel.updated": "{actor} changed the hostel[ {name}]",
    "hostel.rooms_beds_imported": "{actor} imported rooms and beds[ into {name}]",
    "leave.requested": "{actor} requested {leave}{for_student}",
    "leave.emergency_created": "{actor} recorded {leave}{for_student}",
    "leave.approved": "{actor} approved {leave}{for_student}",
    "leave.rejected": "{actor} rejected {leave}{for_student}",
    "leave.cancelled": "{actor} cancelled {leave}{for_student}",
    "ledger.charge_added": "{actor} added a charge[ of {amount}]{for_student}",
    "ledger.fine_added": "{actor} added a fine[ of {amount}]{for_student}",
    "ledger.late_fee_added": "{actor} added a late fee[ of {amount}]{for_student}",
    "ledger.waiver_added": "{actor} waived[ {amount}]{for_student}",
    "ledger.entry_reversed": "{actor} reversed an entry[ of {amount}]{for_student}",
    "mess_menu.saved": "{actor} saved the mess menu[ for {hostel}]",
    "mess_menu.copied": "{actor} copied a week's mess menu[ for {hostel}]",
    "notification_preference.updated": "{actor} changed their email preferences",
    "organization.registered": "{actor} registered the organization[ {name}]",
    "organization.verified": "{actor} confirmed the organization's email address",
    "organization.updated": "{actor} changed the organization's details",
    "organization.suspended": "{actor} suspended the organization",
    "organization.reactivated": "{actor} reactivated the organization",
    "payment.recorded": "{actor} recorded a[ {method}] payment[ of {amount}]{from_student}",
    "payment.reversed": "{actor} reversed a payment[ of {amount}]{from_student}",
    "payments.exported": "{actor} exported payments to a CSV file",
    "payment_submission.created": "{actor} submitted a UPI payment[ of {amount}]{for_student}",
    "payment_submission.verified": "{actor} verified a UPI payment[ of {amount}]{from_student}",
    "payment_submission.rejected": "{actor} rejected a UPI payment[ of {amount}]{from_student}",
    "platform.audit_viewed": "{actor} viewed the audit log",
    "platform.setting_changed": "{actor} changed a platform setting",
    "platform_user.created": "The Platform Owner account was created",
    "platform_user.password_reset_from_console": "The Platform Owner's password was reset on the server",
    "refund.requested": "{actor} requested a refund[ of {amount}]{for_student}",
    "refund.approved": "{actor} approved a refund[ of {amount}]{for_student}",
    "refund.self_approved_sole_administrator": "{actor} approved their own refund request[ of {amount}]{for_student}",
    "refund.rejected": "{actor} rejected a refund[ of {amount}]{for_student}",
    "refund.cancelled": "{actor} cancelled a refund request[ of {amount}]{for_student}",
    "refund.paid": "{actor} paid a refund[ of {amount}][ by {method}]{to_student}",
    "room.updated": "{actor} changed {room}[: now {status}]",
    "room_type.created": "{actor} added the room type[ {name}]",
    "room_type.updated": "{actor} changed the room type[ {name}]",
    "session.reuse_detected": "A copied sign-in was spotted for {person}, so all of their sessions were ended",
    "staff.role_granted": "{person} became the Administrator",
    "staff.updated": "{actor} changed {person_s} details",
    "student.admitted": "{actor} admitted {student}",
    "student.updated": "{actor} changed {student_s} details",
    "student.exited": "{actor} recorded {student_s} exit",
    "student.emergency_contact_updated": "{actor} updated {student_s} emergency contact",
    "student.guardian_added": "{actor} added a guardian for {student}",
    "student.guardian_updated": "{actor} changed a guardian of {student}",
    "student_document.added": "{actor} added a document for {student}",
    "student_document.downloaded": "{actor} downloaded a document of {student}",
    "student_document.verified": "{actor} verified a document of {student}",
    "student_request.submitted": "{applicant} sent a registration request",
    "student_request.verified": "{applicant} confirmed the email address of their registration request",
    "student_request.approved": "{actor} approved {applicant_s} registration request",
    "student_request.rejected": "{actor} rejected {applicant_s} registration request",
    "user.updated": "{actor} changed their own details",
    "user.password_changed": "{person} changed their password",
    "user.password_reset": "{person} reset their password with an emailed code",
}
DEFAULTS = {
    "actor": "Someone",
    "student": "a student",
    "student_s": "a student's",
    "person": "A user",
    "person_s": "a user's",
    "applicant": "Someone",
    "applicant_s": "someone's",
    "bed": "a bed",
    "room": "a room",
    "leave": "leave",
    "for_student": "",
    "from_student": "",
    "to_student": "",
}
_SEGMENT = re.compile(r"\[([^\]]*)\]")
_KEY = re.compile(r"\{(\w+)\}")


def render(template: str, ctx: Mapping[str, str | None]) -> str:
    def segment(m: re.Match[str]) -> str:
        inner = m.group(1)
        return "" if any(not ctx.get(k) for k in _KEY.findall(inner)) else inner

    text = _SEGMENT.sub(segment, template)
    text = _KEY.sub(lambda m: ctx.get(m.group(1)) or DEFAULTS.get(m.group(1), ""), text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:1].upper() + text[1:]


def fallback_sentence(action: str) -> str:
    """For a code with no sentence yet: 'foo.bar_baz' -> '{actor}: foo bar baz'."""
    return "{actor}: " + action.replace(".", " ").replace("_", " ")


# --- One row ---------------------------------------------------------------------------------------------------


def _possessive(name: str) -> str:
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def _value(field_name: str, value: Any, lk: Lookups, zone: ZoneInfo) -> str:
    if value is None or value == "" or value == []:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if field_name in _MONEY_FIELDS:
        return rupees(value) or str(value)
    if isinstance(value, list):
        return ", ".join(_value(field_name, v, lk, zone) for v in value)
    if isinstance(value, dict):
        return ", ".join(f"{label(k)}: {_value(k, v, lk, zone)}" for k, v in value.items())
    if isinstance(value, str):
        if _DATE.match(value):
            return day(value)
        if _DATETIME.match(value):
            try:
                return when(value, zone)
            except ValueError:
                return value
        u = _uuid(value)
        if u is not None:
            if u in lk.names:
                return lk.names[u]
            if u in lk.rows.get("bed", {}):
                return _bed(lk, u) or "another bed"
            return "another record"
        return words(value)
    return str(value)


def _bed(lk: Lookups, bed_id: uuid.UUID | None) -> str | None:
    bed = lk.rows["bed"].get(bed_id) if bed_id else None
    if bed is None:
        return None
    room = lk.rows["room"].get(bed["room_id"])
    return f"bed {bed['label']} in room {room['number']}" if room else f"bed {bed['label']}"


@dataclass
class Described:
    category: str
    sentence: str
    actor_name: str
    actor_kind: str  # administrator | student | system | platform
    summary: str | None
    details: list[dict[str, str | None]]
    student_id: uuid.UUID | None
    student_name: str | None
    link_label: str | None
    link_to: str | None


def describe(r: AuditLog, lk: Lookups, zone: ZoneInfo) -> Described:
    before = r.before_values or {}
    after = r.after_values or {}
    ctx: dict[str, str | None] = {}

    # who did it
    if r.actor_type == "system":
        actor_name, actor_kind, ctx["actor"] = "System", "system", "The system"
    elif r.actor_type == "platform_user":
        actor_name, actor_kind, ctx["actor"] = "Platform Owner", "platform", "The Platform Owner"
    else:
        name, kind = lk.users.get(r.actor_user_id, ("Someone", "staff")) if r.actor_user_id else ("Someone", "staff")
        actor_name, actor_kind, ctx["actor"] = name, "student" if kind == "student" else "administrator", name

    # what it points at
    row = lk.rows.get(r.entity_type, {}).get(r.entity_id) if r.entity_id else None
    student_id: uuid.UUID | None = None
    if r.entity_type == "student":
        student_id = r.entity_id
    elif r.entity_type == "user" and r.entity_id:
        student_id = lk.student_by_user.get(r.entity_id)
    elif row is not None:
        student_id = row.get("student_id")
    student = lk.students.get(student_id) if student_id else None
    if student is None:
        student_id = None
    else:
        is_self = student[2] == r.actor_user_id
        ctx["student"] = student[0]
        ctx["student_s"] = "their" if is_self else _possessive(student[0])
        if not is_self:
            ctx["for_student"] = f" for {student[0]}"
            ctx["from_student"] = f" from {student[0]}"
            ctx["to_student"] = f" to {student[0]}"

    if r.entity_type == "user" and r.entity_id and r.entity_id in lk.users:
        ctx["person"] = lk.users[r.entity_id][0]
        ctx["person_s"] = _possessive(lk.users[r.entity_id][0])
    if row is not None:
        if row.get("amount") is not None:
            ctx["amount"] = rupees(row["amount"])
        method = row.get("method") or row.get("paid_method")
        if method:
            ctx["method"] = words(method)
        if r.entity_type == "bed_allocation":
            ctx["bed"] = _bed(lk, _uuid(after.get("bed_id")) or row.get("bed_id"))
        if r.entity_type == "leave_request":
            kind = LEAVE.get(row["leave_type"], "leave")
            start, end = row.get("starts_at"), row.get("ends_at")
            dates = ""
            if start and end:
                a, b = f"{start.astimezone(zone):%d-%m-%Y}", f"{end.astimezone(zone):%d-%m-%Y}"
                dates = f" on {a}" if a == b else f" from {a} to {b}"
            ctx["leave"] = f"{kind}{dates}"
        if r.entity_type == "student_request" and row.get("full_name"):
            ctx["applicant"] = row["full_name"]
            ctx["applicant_s"] = _possessive(row["full_name"])
        if r.entity_type == "file":
            ctx["file"] = words(row["category"])
        if r.entity_type == "announcement":
            ctx["title"] = row["title"]
    if r.entity_type == "bed":
        ctx["bed"] = _bed(lk, r.entity_id)
    if r.entity_type == "room" and row is not None:
        ctx["room"] = f"room {row['number']}"
    if "status" in after and isinstance(after["status"], str):
        ctx["status"] = words(after["status"])
    ctx["title"] = ctx.get("title") or after.get("title") or before.get("title")
    ctx["name"] = (lk.names.get(r.entity_id) if r.entity_id else None) or after.get("name") or before.get("name")
    if r.hostel_id and r.hostel_id in lk.names:
        ctx["hostel"] = lk.names[r.hostel_id]

    template = SENTENCES.get(r.action) or fallback_sentence(r.action)
    sentence = render(template, ctx)

    # What changed. Unchanged fields are hidden. An EDIT (stored with a before-value, even an empty one)
    # also lists fields whose values the allowlist didn't keep ("Changed phone and program"); for a
    # creation those names say nothing the sentence doesn't, so only kept values are shown.
    details: list[dict[str, str | None]] = []
    is_update = r.before_values is not None
    # Only a generic edit ("...updated") gains from naming fields whose values weren't kept: elsewhere
    # the sentence already says what changed ("approved" is the status change).
    unkept = set(r.changed_fields or []) if is_update and r.action.endswith("updated") else set()
    fields = set(before) | set(after) | unkept
    for f in sorted(fields, key=lambda k: (_ORDER.get(k, len(_ORDER)), k)):
        if f in before and f in after and before[f] == after[f]:
            continue
        kept = f in before or f in after
        details.append(
            {
                "field": f,
                "label": label(f),
                "before": _value(f, before[f], lk, zone) if f in before else None,
                "after": _value(f, after[f], lk, zone) if f in after else (None if kept else "not recorded"),
            }
        )
    summary: str | None = None
    full = [d for d in details if d["before"] is not None and d["after"] not in (None, "not recorded")]
    if details and is_update and len(full) == len(details) <= 2:
        summary = " · ".join(f"{d['label']}: {d['before']} → {d['after']}" for d in details)
    elif details and is_update:
        names = [str(d["label"]) if str(d["label"]).isupper() else str(d["label"]).lower() for d in details]
        shown = names[:3] + ([f"{len(names) - 3} more"] if len(names) > 3 else [])
        summary = "Changed " + (", ".join(shown[:-1]) + " and " + shown[-1] if len(shown) > 1 else shown[0])
    elif details:
        summary = " · ".join(f"{d['label']}: {d['after']}" for d in details[:3])

    link_label, link_to = _link(r, row, student_id, lk)
    return Described(
        category=category_of(r.action),
        sentence=sentence,
        actor_name=actor_name,
        actor_kind=actor_kind,
        summary=summary,
        details=details,
        student_id=student_id,
        student_name=student[0] if student else None,
        link_label=link_label,
        link_to=link_to,
    )


def _link(
    r: AuditLog, row: dict[str, Any] | None, student_id: uuid.UUID | None, lk: Lookups
) -> tuple[str | None, str | None]:
    """Where to open the affected record, only while it still exists."""
    if student_id is not None:
        return "Open student", f"/admin/students/{student_id}"
    e = r.entity_type
    if e == "hostel" and r.entity_id in lk.names:
        return "Open hostel", f"/admin/hostels/{r.entity_id}"
    if e in ("bed", "room") and row is not None:
        return "Open hostel", f"/admin/hostels/{row['hostel_id']}"
    if e == "student_request" and row is not None:
        return "Open registration requests", "/admin/registration-requests"
    if e == "leave_request" and row is not None:
        return "Open leave", "/admin/leave"
    if e == "fee_structure" and r.entity_id in lk.names:
        return "Open fees", "/admin/fee-structures"
    if e == "announcement" and row is not None:
        return "Open announcements", "/admin/announcements"
    if e == "mess_menu" and r.entity_id in lk.names:
        return "Open mess menu", "/admin/mess"
    if e == "complaint_category" and r.entity_id in lk.names:
        return "Open complaints", "/admin/complaints"
    if e in ("organization", "academic_session", "academic_term"):
        return "Open organization", "/admin/organization"
    return None, None
