"""Roles and permissions (docs/02 §7, docs/04 §6; D75).

ONE organization role since D75: Administrator. It is stored as `super_admin` (the role that already did
everything, Q59) and shown as "Administrator" on every screen. An Administrator holds every permission in
every hostel of the organization; two-step verification is mandatory for it (Q16).

The permission names stay: every route still declares the permission it needs, so the generated security
suite keeps checking that a student (or a signed-out caller) can never use an Administrator route, and an
organization's Administrator can never reach another organization (RLS, 05 MT-*).

The removed roles (Hostel Manager, Warden, Accountant, Security Guard) can't be granted any more: the
database refuses them (migration 0008). Their old grants were revoked by that migration.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

SUPER_ADMIN = "super_admin"  # shown as "Administrator" (D75)
ADMINISTRATOR = SUPER_ADMIN
ROLES = (SUPER_ADMIN,)

# --- Permissions ---------------------------------------------------------------------------------------------
ORG_MANAGE = "organization.manage"  # organization profile, academic calendar
AUDIT_READ = "audit.read"  # the audit log (Q17)
HOSTEL_CREATE = "hostels.create"
HOSTEL_VIEW = "hostels.view"
HOSTEL_MANAGE = "hostels.manage"  # structure, settings, CSV import
BED_BOARD_VIEW = "beds.view_board"
STAFF_MANAGE = "staff.manage"  # add, edit, deactivate Administrators
STUDENTS_VIEW = "students.view"
STUDENT_CONTACTS_VIEW = "students.view_contacts"  # guardians, address, date of birth
STUDENTS_MANAGE = "students.manage"  # admission, edits, documents, exit
STUDENT_DOCUMENTS = "students.identity_documents"  # open restricted identity documents (Q62)
ALLOCATIONS_MANAGE = "allocations.manage"  # allocate, reserve, check in/out, transfer
FINANCE_VIEW = "finance.view"  # balances, ledgers, payments
FINANCE_MANAGE = "finance.manage"  # fee structures, charges, payments, waivers, reversals, refunds
REFUNDS_APPROVE = "refunds.approve"  # decide refunds: another Administrator, or a sole one (Q30, D75)
LEAVE_MANAGE = "leave.manage"  # leave queue, approve/reject, emergency leave (Q35)
COMPLAINTS_MANAGE = "complaints.manage"  # queue, assign, start, resolve, comment (Q40)
MESS_MANAGE = "mess.manage"  # weekly menus, copy (Q41)
ANNOUNCEMENTS_MANAGE = "announcements.manage"

# Permissions that only make sense organization-wide (not per hostel).
ORG_LEVEL = frozenset({ORG_MANAGE, AUDIT_READ, HOSTEL_CREATE})
HOSTEL_LEVEL: frozenset[str] = frozenset(
    {
        HOSTEL_VIEW,
        HOSTEL_MANAGE,
        BED_BOARD_VIEW,
        STAFF_MANAGE,
        STUDENTS_VIEW,
        STUDENT_CONTACTS_VIEW,
        STUDENTS_MANAGE,
        STUDENT_DOCUMENTS,
        ALLOCATIONS_MANAGE,
        FINANCE_VIEW,
        FINANCE_MANAGE,
        REFUNDS_APPROVE,
        LEAVE_MANAGE,
        COMPLAINTS_MANAGE,
        MESS_MANAGE,
        ANNOUNCEMENTS_MANAGE,
    }
)

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {SUPER_ADMIN: ORG_LEVEL | HOSTEL_LEVEL}
ALL_PERMISSIONS = frozenset().union(*ROLE_PERMISSIONS.values())


@dataclass(frozen=True)
class RoleGrant:
    role: str
    hostel_id: uuid.UUID | None  # always None since D75 (organization-wide)


@dataclass(frozen=True)
class Principal:
    """Who is calling, loaded fresh from the database on every request (never from the token)."""

    user_id: uuid.UUID
    org_id: uuid.UUID
    session_id: uuid.UUID
    kind: str  # 'staff' | 'student'
    read_only: bool = False
    grants: tuple[RoleGrant, ...] = field(default_factory=tuple)

    @property
    def roles(self) -> frozenset[str]:
        return frozenset(g.role for g in self.grants)

    @property
    def is_super_admin(self) -> bool:
        return SUPER_ADMIN in self.roles

    is_administrator = is_super_admin

    def permissions(self) -> frozenset[str]:
        return frozenset().union(*(ROLE_PERMISSIONS.get(g.role, frozenset()) for g in self.grants))

    def has(self, permission: str) -> bool:
        return permission in self.permissions()

    def hostels_for(self, permission: str) -> frozenset[uuid.UUID] | None:
        """Hostels where `permission` may be used. None = every hostel of the organization (an
        Administrator); an empty set = nowhere (a student, or a staff account without the role).
        Kept as the one place services ask, though since D75 the answer is all or nothing."""
        return None if self.has(permission) else frozenset()
