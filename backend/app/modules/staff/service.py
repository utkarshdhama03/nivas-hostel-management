"""The Administrator, and activation links (docs/04 §12.6; Q69; D75, D76).

Since D75 the one organization role is Administrator (stored as `super_admin`), organization-wide. Since
D76 each organization has exactly one: the person who registered it. The database allows at most one
active Administrator grant per organization (index role_assignments_one_administrator, migration 0009),
so nothing here adds, removes or re-activates one. A lost authenticator is reset by the Platform Owner.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core import audit, jobs
from app.core.crud import not_found
from app.core.errors import ProblemError
from app.core.permissions import SUPER_ADMIN, Principal
from app.modules.identity.models import RoleAssignment, StaffMember, User
from app.modules.organizations.models import Organization


def get_staff(db: Session, p: Principal, staff_id: uuid.UUID, *, lock: bool = False) -> tuple[StaffMember, User]:
    q = select(StaffMember).where(StaffMember.id == staff_id)
    staff = db.scalars(q.with_for_update() if lock else q).one_or_none()
    if staff is None:
        raise not_found()
    user = db.get(User, staff.user_id)
    assert user is not None
    return staff, user


# --- Administrators --------------------------------------------------------------------------------------


def lock_organization(db: Session, org_id: uuid.UUID) -> None:
    db.execute(select(Organization.id).where(Organization.id == org_id).with_for_update())


def active_administrator_count(db: Session) -> int:
    """Active Administrators of the current organization (RLS scopes the query to it): 1 since D76."""
    q = (
        select(func.count())
        .select_from(RoleAssignment)
        .join(User, User.id == RoleAssignment.user_id)
        .where(RoleAssignment.role == SUPER_ADMIN, RoleAssignment.revoked_at.is_(None), User.status == "active")
    )
    return int(db.scalar(q) or 0)


def resend_activation_email(db: Session, p: Principal, user: User) -> None:
    """D80: queue the activation email again for a student who hasn't activated yet."""
    if user.kind != "student" or user.status != "pending_activation":
        raise ProblemError(409, "already_activated", "This account is already active")
    jobs.enqueue(db, "email.activation", {"user_id": str(user.id)}, org_id=user.org_id, priority=jobs.PRIORITY_SECURITY)
    audit.record(db, p, "activation_email.sent", "user", user.id)


def is_active_administrator(db: Session, user_id: uuid.UUID) -> bool:
    """An active staff account holding the Administrator role (any hostel: D75)."""
    return bool(
        db.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM role_assignments ra JOIN users u ON u.id = ra.user_id "
                "WHERE ra.user_id = :u AND ra.revoked_at IS NULL AND ra.role = 'super_admin' "
                "AND u.status = 'active' AND u.kind = 'staff')"
            ),
            {"u": user_id},
        )
    )
