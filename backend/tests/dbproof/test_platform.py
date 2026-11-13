"""The Platform Owner's database window (Q15, Q16, Q55; docs/05 MT-10, AUTH-15, RBAC-10)."""

import secrets
import uuid
from typing import Any

import psycopg
import pytest
from psycopg import sql

from tests.conftest import Connect

from .catalog import PLATFORM_WINDOW
from .helpers import INSUFFICIENT_PRIVILEGE, RAISE_EXCEPTION, expect_db_error, tenant_tx
from .world import World

pytestmark = pytest.mark.dbproof


@pytest.fixture
def platform(connect: Connect) -> psycopg.Connection[Any]:
    return connect("hms_platform")


@pytest.fixture
def platform_user(platform: psycopg.Connection[Any]) -> uuid.UUID:
    row = platform.execute(
        "INSERT INTO platform_users (email, full_name, password_hash) VALUES (%s, 'Platform Owner', 'x') RETURNING id",
        (f"owner-{secrets.token_hex(4)}@platform.test",),
    ).fetchone()
    platform.commit()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


def test_platform_sees_counts_across_organizations(
    platform: psycopg.Connection[Any], world_a: World, world_b: World
) -> None:
    rows = dict(
        platform.execute(
            "SELECT org_id, count(*) FROM students WHERE org_id IN (%s, %s) GROUP BY org_id", (world_a.org, world_b.org)
        ).fetchall()
    )
    assert rows == {world_a.org: 3, world_b.org: 3}  # s1, s2 and the spare s3 of each world
    platform.rollback()


def test_platform_cannot_select_masked_columns(su: psycopg.Connection[Any], connect: Connect, world_a: World) -> None:
    """Every column outside the window (docs/03 §6) raises a permission error. Checked one by one."""
    failures: list[str] = []
    for table, allowed in PLATFORM_WINDOW.items():
        columns = [
            r[0]
            for r in su.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s",
                (table,),
            ).fetchall()
        ]
        for column in columns:
            conn = connect("hms_platform")
            query = sql.SQL("SELECT {} FROM {} LIMIT 1").format(sql.Identifier(column), sql.Identifier(table))
            try:
                conn.execute(query)
                permitted = True
            except psycopg.errors.InsufficientPrivilege:
                permitted = False
            conn.close()
            should = allowed is None or column in allowed
            if permitted != should:
                failures.append(f"{table}.{column}: permitted={permitted}, expected={should}")
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize(
    "table", ["files", "student_documents", "student_guardians", "ledger_entries", "payments", "rooms", "sessions"]
)
def test_platform_cannot_read_tables_outside_window(platform: psycopg.Connection[Any], table: str) -> None:
    with expect_db_error(INSUFFICIENT_PRIVILEGE):
        platform.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
    platform.rollback()


def test_platform_audit_view_shows_actions_not_values(platform: psycopg.Connection[Any], world_a: World) -> None:
    row = platform.execute(
        "SELECT action, entity_type, changed_fields FROM audit_logs WHERE org_id = %s AND action = 'hostel.created'",
        (world_a.org,),
    ).fetchone()
    assert row == ("hostel.created", "hostel", ["name"])
    platform.rollback()
    with expect_db_error(INSUFFICIENT_PRIVILEGE):
        platform.execute("SELECT after_values FROM audit_logs LIMIT 1")
    platform.rollback()


def test_app_cannot_read_platform_tables(app_conn: psycopg.Connection[Any]) -> None:
    with expect_db_error(INSUFFICIENT_PRIVILEGE):
        app_conn.execute("SELECT count(*) FROM platform_users")
    app_conn.rollback()


# --- Organization status: only the Platform Owner suspends (Q14) ------------------------------


def test_app_cannot_suspend_organization(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(RAISE_EXCEPTION), tenant_tx(app_conn, fresh.org) as cur:
        cur.execute("UPDATE organizations SET status = 'suspended' WHERE id = %s", (fresh.org,))


def test_app_cannot_write_suspension_columns(app_conn: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(INSUFFICIENT_PRIVILEGE), tenant_tx(app_conn, fresh.org) as cur:
        cur.execute("UPDATE organizations SET suspended_at = now() WHERE id = %s", (fresh.org,))


def test_platform_can_suspend_and_reactivate(platform: psycopg.Connection[Any], fresh: World) -> None:
    platform.execute(
        "UPDATE organizations SET status = 'suspended', suspended_at = now(), "
        "suspension_reason = 'Unpaid test' WHERE id = %s",
        (fresh.org,),
    )
    platform.commit()
    platform.execute(
        "UPDATE organizations SET status = 'active', suspended_at = NULL, suspension_reason = NULL WHERE id = %s",
        (fresh.org,),
    )
    platform.commit()
    row = platform.execute("SELECT status FROM organizations WHERE id = %s", (fresh.org,)).fetchone()
    assert row == ("active",)
    platform.rollback()


def test_invalid_status_transition_rejected(platform: psycopg.Connection[Any], fresh: World) -> None:
    with expect_db_error(RAISE_EXCEPTION):
        platform.execute("UPDATE organizations SET status = 'pending_verification' WHERE id = %s", (fresh.org,))
    platform.rollback()


# --- No authenticator reset any more (D87) -----------------------------------------------------


def test_the_authenticator_reset_function_is_gone(platform: psycopg.Connection[Any]) -> None:
    """D87: no two-step verification, so platform_reset_super_admin_mfa() was dropped (migration 0017)."""
    row = platform.execute("SELECT count(*) FROM pg_proc WHERE proname = 'platform_reset_super_admin_mfa'").fetchone()
    assert row == (0,)
