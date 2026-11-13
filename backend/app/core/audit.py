"""Audit service (docs/02 B5, brief §9).

    audit.record(db, actor, "staff.role_granted", "user", user.id,
                 before={...}, after={...})

* Written by the service layer, in the SAME transaction as the change: if the change
  rolls back, so does its audit row, and the reverse. There's no way to save one
  without the other.
* Before/after values pass through an ALLOWLIST per entity type. A field that isn't
  listed is dropped, so a password hash, token or TOTP secret can't reach the audit
  log even if a caller passes the whole row by mistake. Unlisted entity types
  record field NAMES only.
* Request metadata (request ID, IP, user agent) comes from the request context
  (app/core/request_state.py), never from caller-supplied values.
* The table is append-only: the app roles have INSERT/SELECT only and a trigger
  rejects UPDATE/DELETE (03 §9).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core import request_state
from app.core.models import AuditLog
from app.core.permissions import Principal

# entity_type -> fields whose VALUES may be recorded. Phases add their entities here.
AUDITABLE_FIELDS: dict[str, frozenset[str]] = {
    # Exit (Q22, Q93). Before this entry existed, student.exited rows recorded no values at all.
    "student": frozenset(
        {"exited_on", "exit_reason", "status", "outstanding_dues", "deposit_held", "outstanding_note"}
    ),
    "organization": frozenset(
        {
            "name",
            "slug",
            "status",
            "timezone",
            "suspension_reason",
            "legal_name",
            "contact_email",
            "contact_phone",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "postal_code",
            "upi_id",
            "upi_payee_name",
            "accepts_student_requests",
        }
    ),
    "user": frozenset(
        {
            "kind",
            "full_name",
            "email",
            "phone",
            "status",
            "read_only_until",
            "locked_until",
            "employee_code",
            "designation",
            "joined_on",
            "left_on",
            "delivery",
            "reactivation_reason",
        }
    ),
    "role_assignment": frozenset({"role", "revoked_at"}),
    "session": frozenset({"revoked_at", "revoke_reason"}),
    "notification_preference": frozenset({"category", "email_enabled"}),
    "academic_session": frozenset({"name", "starts_on", "ends_on", "is_current"}),
    "academic_term": frozenset({"name", "starts_on", "ends_on"}),
    "hostel": frozenset(
        {
            "code",
            "name",
            "resident_type",
            "address",
            "local_outing_requires_approval",
            "is_active",
            "buildings",
            "floors",
            "rooms",
            "beds",
        }
    ),
    "room_type": frozenset({"name", "description", "beds_per_room", "is_active"}),
    "room": frozenset({"status", "notes", "room_type_id"}),
    "bed": frozenset({"status"}),
    "student_request": frozenset({"status", "reject_reason", "student_code"}),  # D74; no personal data
    "refund_request": frozenset({"status", "self_approved"}),  # D75: a sole Administrator's self-approval is visible
    "complaint_category": frozenset({"name", "is_active"}),
    "mess_menu": frozenset({"entries", "from_hostel_id", "from_week_start", "to_week_start", "copied"}),
    "announcement": frozenset({"title", "audience"}),
    "report": frozenset({"report", "start", "end"}),
    "platform_setting": frozenset({"key", "value"}),
}
# Never recorded, whatever the allowlist says (defence in depth against a careless edit above).
FORBIDDEN_FIELDS = frozenset(
    {
        "password_hash",
        "password",
        "totp_secret_encrypted",
        "totp_secret",
        "token",
        "token_hash",
        "refresh_token_hash",
        "code_hash",
        "otp",
        "secret",
    }
)


@dataclass(frozen=True)
class SystemActor:
    """The worker or scheduler acting on its own."""


@dataclass(frozen=True)
class PlatformActor:
    platform_user_id: uuid.UUID


SYSTEM = SystemActor()
Actor = Principal | PlatformActor | SystemActor


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID | Decimal):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, timedelta):
        return int(value.total_seconds() // 60)  # minutes
    if isinstance(value, list | tuple | set | frozenset):
        return [_jsonable(v) for v in value]
    return value


def _filter(entity_type: str, values: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if values is None:
        return None
    allowed = AUDITABLE_FIELDS.get(entity_type, frozenset())
    return {k: _jsonable(v) for k, v in values.items() if k in allowed and k not in FORBIDDEN_FIELDS}


def record(
    db: Session,
    actor: Actor,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    *,
    org_id: uuid.UUID | None = None,
    hostel_id: uuid.UUID | None = None,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
) -> AuditLog:
    """Add one audit row to the current transaction. Flushes, so a rejected row fails the
    caller's transaction right here rather than at commit."""
    keys = set(before or {}) | set(after or {})
    changed = sorted(k for k in keys if (before or {}).get(k) != (after or {}).get(k) and k not in FORBIDDEN_FIELDS)

    row = AuditLog(
        hostel_id=hostel_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        changed_fields=changed,
        before_values=_filter(entity_type, before),
        after_values=_filter(entity_type, after),
        ip_address=request_state.valid_client_ip(),
        user_agent=request_state.user_agent.get(),
    )
    rid = request_state.request_id.get()
    row.request_id = uuid.UUID(rid) if rid else None

    if isinstance(actor, Principal):
        row.actor_type, row.actor_user_id = "user", actor.user_id
        row.actor_roles = sorted(actor.roles)
        row.org_id = actor.org_id  # always the caller's own organization
    elif isinstance(actor, PlatformActor):
        row.actor_type, row.actor_platform_user_id, row.org_id = "platform_user", actor.platform_user_id, org_id
    else:
        row.actor_type, row.org_id = "system", org_id
    db.add(row)
    db.flush()
    return row
