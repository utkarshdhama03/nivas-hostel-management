"""Row-Level Security really isolates organizations (docs/05 MT-1, MT-2, MT-4, §3.1)."""

from typing import Any

import psycopg
import pytest
from psycopg import sql

from tests.conftest import Connect

from .catalog import TENANT_TABLES
from .helpers import INSUFFICIENT_PRIVILEGE, expect_db_error, ins, set_tenant, tenant_tx
from .world import World

pytestmark = pytest.mark.dbproof


def count(conn: psycopg.Connection[Any], table: str, where: str = "true", *params: Any) -> int:
    q = sql.SQL("SELECT count(*) FROM {} WHERE " + where).format(sql.Identifier(table))
    row = conn.execute(q, params).fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.parametrize("role", ["hms_app", "hms_worker"])
@pytest.mark.parametrize("table", TENANT_TABLES)
def test_rls_returns_zero_rows_without_context(
    table: str, role: str, connect: Connect, su: psycopg.Connection[Any], world_a: World, world_b: World
) -> None:
    # The data really exists (seen by the superuser, who bypasses RLS) ...
    assert count(su, table, "org_id IN (%s, %s)", world_a.org, world_b.org) > 0
    # ... but an app role with no tenant set sees none of it: fail closed.
    conn = connect(role)
    assert count(conn, table) == 0
    conn.rollback()


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_rls_hides_other_org_rows(table: str, connect: Connect, world_a: World, world_b: World) -> None:
    conn = connect("hms_app")
    set_tenant(conn, world_a.org)
    assert count(conn, table, "org_id = %s", world_a.org) > 0  # own rows visible
    assert count(conn, table, "org_id <> %s", world_a.org) == 0  # nobody else's
    conn.rollback()


def test_rls_rejects_insert_for_other_org(app_conn: psycopg.Connection[Any], world_a: World, world_b: World) -> None:
    with expect_db_error(INSUFFICIENT_PRIVILEGE), tenant_tx(app_conn, world_a.org) as cur:
        ins(cur, "complaint_categories", org_id=world_b.org, name="Planted by A")


def test_rls_rejects_update_into_other_org(app_conn: psycopg.Connection[Any], world_a: World, world_b: World) -> None:
    with expect_db_error(INSUFFICIENT_PRIVILEGE), tenant_tx(app_conn, world_a.org) as cur:
        cur.execute("UPDATE academic_terms SET org_id = %s WHERE id = %s", (world_b.org, world_a.term))


def test_rls_update_of_other_org_rows_touches_nothing(
    app_conn: psycopg.Connection[Any], world_a: World, world_b: World, su: psycopg.Connection[Any]
) -> None:
    with tenant_tx(app_conn, world_a.org) as cur:
        cur.execute("UPDATE complaint_categories SET name = 'hacked' WHERE org_id = %s", (world_b.org,))
        assert cur.rowcount == 0
    assert count(su, "complaint_categories", "name = 'hacked'") == 0


def test_tenant_setting_cleared_after_commit(app_conn: psycopg.Connection[Any], world_a: World) -> None:
    set_tenant(app_conn, world_a.org)
    assert count(app_conn, "students") > 0
    app_conn.commit()  # the transaction ends -> the setting must be gone
    row = app_conn.execute("SELECT current_setting('app.org_id', true)").fetchone()
    assert row is not None and row[0] in ("", None)
    assert count(app_conn, "students") == 0
    app_conn.rollback()


def test_organizations_directory_readable_without_context(app_conn: psycopg.Connection[Any], world_a: World) -> None:
    """Needed to resolve the org slug before login (docs/04 §4.2)."""
    row = app_conn.execute("SELECT id, status FROM organizations WHERE slug = %s", (world_a.slug,)).fetchone()
    assert row == (world_a.org, "active")
    app_conn.rollback()


def test_app_cannot_delete(app_conn: psycopg.Connection[Any], world_a: World) -> None:
    with expect_db_error(INSUFFICIENT_PRIVILEGE), tenant_tx(app_conn, world_a.org) as cur:
        cur.execute("DELETE FROM complaint_categories WHERE id = %s", (world_a.category,))


def test_worker_can_claim_jobs_of_every_org(connect: Connect, world_a: World, world_b: World) -> None:
    worker = connect("hms_worker")
    assert count(worker, "jobs", "org_id IN (%s, %s)", world_a.org, world_b.org) >= 2
    worker.rollback()
    app = connect("hms_app")
    assert count(app, "jobs") == 0  # the API role, without context, sees no jobs
    app.rollback()


def test_worker_cross_org_policy_only_on_jobs(su: psycopg.Connection[Any]) -> None:
    tables = {
        r[0]
        for r in su.execute(
            "SELECT tablename FROM pg_policies WHERE 'hms_worker' = ANY(roles) AND cmd IN ('ALL', 'SELECT', 'UPDATE')"
        ).fetchall()
    }
    assert tables == {"jobs"}
