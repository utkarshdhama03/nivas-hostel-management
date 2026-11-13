"""The business clock (D67, 03 §1).

app_now() is business time: the transaction-local app.now that app/core/db.py sets from the
application clock, or the real now() when unset. Business tables default to it. Security tables
(audit, login attempts, sessions, tokens, MFA, recovery codes, QR redemptions, role grants, users,
platform accounts, jobs, backup runs) keep the real now(): these tests prove that setting app.now,
which any application code could do, cannot backdate them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import time_machine
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core import audit
from app.core.db import transaction
from app.modules.identity.models import User
from tests.support import add_person, make_org

BACKDATED = "2000-01-01T00:00:00+00:00"

# Every table with a time default, and which clock it follows. A new table with a time default fails
# test_every_time_default_is_classified until someone decides which list it belongs to.
SECURITY = {
    "audit_logs", "auth_tokens", "backup_runs", "email_otps", "jobs", "login_attempts",
    "platform_sessions", "platform_settings", "platform_users",
    "role_assignments", "sessions", "users",
    "student_registration_requests",  # D74: verification window, spam limits, purge
}  # fmt: skip
BUSINESS = {
    "academic_sessions", "academic_terms", "announcements",
    "bed_allocations", "beds", "buildings",
    "complaint_attachments", "complaint_categories", "complaint_updates", "complaints",
    "fee_structures", "files", "floors", "hostels",
    "leave_requests", "ledger_allocations", "ledger_entries", "mess_menus", "notification_preferences",
    "notifications", "organization_profiles", "organizations", "payment_reversals",
    "payment_submissions", "payments", "refund_requests", "room_types", "rooms", "staff_members",
    "student_documents", "student_guardians", "students",
}  # fmt: skip


def _backdate(db: Session) -> None:
    """What a careless or malicious piece of application code could do inside its transaction."""
    db.execute(text("SELECT set_config('app.now', :t, true)"), {"t": BACKDATED})


def _real(db: Session) -> datetime:
    return db.scalar(text("SELECT clock_timestamp()"))  # the wall clock: unaffected by app.now


def _assert_real(value: Any, real: datetime, what: str) -> None:
    assert value is not None, what
    assert abs(value - real) < timedelta(minutes=5), f"{what} = {value}, expected about {real}"


def test_app_now_follows_app_now_setting_and_falls_back_to_now(app_db: str) -> None:
    with transaction(None) as db:
        set_by_app = db.scalar(text("SELECT app_now()"))
        assert abs(set_by_app - datetime.now(UTC)) < timedelta(seconds=5)  # copied from the (test) clock
        _backdate(db)
        assert db.scalar(text("SELECT app_now()")) == datetime.fromisoformat(BACKDATED)
        db.execute(text("SELECT set_config('app.now', '', true)"))
        assert db.scalar(text("SELECT app_now() = now()"))  # unset: the real transaction time


def test_frozen_application_clock_reaches_business_sql() -> None:
    with time_machine.travel(datetime(2027, 3, 31, 18, 31, tzinfo=UTC), tick=False):  # 1 Apr 00:01 IST
        with transaction(None) as db:
            assert db.scalar(text("SELECT (app_now() AT TIME ZONE 'Asia/Kolkata')::date")).isoformat() == "2027-04-01"


def test_backdating_app_now_moves_business_rows_but_never_security_timestamps(app_db: str) -> None:
    org = make_org()
    person = add_person(org, "clockcheck", kind="student", code="STU-CLOCK-1", roles=())
    staff = add_person(org, "clockstaff")  # no role yet: the check below grants one
    with transaction(org.id) as db:
        _backdate(db)
        real = _real(db)
        # A business row follows app.now.
        cat = db.scalar(
            text("INSERT INTO complaint_categories (org_id, name) VALUES (:o, 'Clock test') RETURNING created_at"),
            {"o": org.id},
        )
        assert cat == datetime.fromisoformat(BACKDATED)
        # Security rows don't.
        entry = audit.record(db, audit.SYSTEM, "student.updated", "student", None, org_id=org.id)
        db.refresh(entry)
        _assert_real(entry.occurred_at, real, "audit_logs.occurred_at")
        checks = {
            "login_attempts.attempted_at": "INSERT INTO login_attempts (org_id, identifier_hash, succeeded) "
            "VALUES (:o, repeat('a', 64), false) RETURNING attempted_at",
            "sessions.created_at": "INSERT INTO sessions (org_id, user_id, family_id, refresh_token_hash, expires_at, "
            "absolute_expires_at) VALUES (:o, :u, gen_random_uuid(), repeat('b', 64), now() + interval '1 day', "
            "now() + interval '2 days') RETURNING created_at",
            "auth_tokens.created_at": "INSERT INTO auth_tokens (org_id, user_id, purpose, token_hash, delivery, "
            "expires_at) VALUES (:o, :u, 'org_registration', repeat('c', 64), 'email', now() + interval '1 hour') "
            "RETURNING created_at",
            "email_otps.created_at": "INSERT INTO email_otps (org_id, user_id, code_hash, expires_at) "
            "VALUES (:o, :u, repeat('d', 64), now() + interval '10 minutes') RETURNING created_at",
            "role_assignments.granted_at": "INSERT INTO role_assignments (org_id, user_id, role) "
            "VALUES (:o, :u, 'super_admin') RETURNING granted_at",
            "users.created_at": "INSERT INTO users (org_id, kind, full_name) VALUES (:o, 'staff', 'Clock') "
            "RETURNING created_at",
        }
        params = {"o": org.id, "u": person.user_id, "g": staff.user_id, "j": uuid.uuid4().hex}
        for what, sql in checks.items():
            uid = staff.user_id if what.startswith("role_assignments") else person.user_id
            _assert_real(db.scalar(text(sql), {**params, "u": uid}), real, what)
        # users.updated_at through the ORM (onupdate): also the real clock.
        user = db.scalars(select(User).where(User.id == staff.user_id)).one()
        user.full_name = "Clock Staff"
        db.flush()
        db.refresh(user)
        _assert_real(user.updated_at, real, "users.updated_at")
    with transaction(None, role="hms_platform") as db:
        _backdate(db)
        real = _real(db)
        pid = db.scalar(
            text(
                "INSERT INTO platform_users (email, full_name, password_hash) "
                "VALUES (:e, 'Clock Owner', 'x') RETURNING id"
            ),
            {"e": f"clock.{uuid.uuid4().hex[:6]}@example.com"},
        )
        for what, sql in {
            "platform_users.created_at": "SELECT created_at FROM platform_users WHERE id = :p",
            "platform_sessions.created_at": "INSERT INTO platform_sessions (platform_user_id, family_id, "
            "refresh_token_hash, expires_at, absolute_expires_at) VALUES (:p, gen_random_uuid(), repeat('f', 64), "
            "now() + interval '1 day', now() + interval '2 days') RETURNING created_at",
        }.items():
            _assert_real(db.scalar(text(sql), {"p": pid}), real, what)


def test_every_time_default_is_classified(su: psycopg.Connection[Any]) -> None:
    """Business tables default to app_now(), security tables to now(); nothing unclassified."""
    rows = su.execute(  # the superuser connection only INSPECTS the catalog (conftest)
        "SELECT table_name, column_name, column_default FROM information_schema.columns "
        "WHERE table_schema = 'public' AND column_default LIKE '%%now()%%'"
    ).fetchall()
    assert rows
    assert not SECURITY & BUSINESS
    for table, column, default in rows:
        where = f"{table}.{column} DEFAULT {default}"
        assert table in SECURITY | BUSINESS, f"unclassified table with a time default: {where}"
        assert default == ("app_now()" if table in BUSINESS else "now()"), where
    assert {t for t, _, _ in rows} == SECURITY | BUSINESS  # the lists hold no stale names
