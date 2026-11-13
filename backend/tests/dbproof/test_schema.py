"""The schema is what docs/03 says it is: right tables, RLS everywhere it must be,
safe roles, and no way to delete business data. (docs/05 MT-6, MT-7)"""

from pathlib import Path
from typing import Any

import psycopg
import pytest

from .catalog import (
    EDITABLE_TABLES,
    PLATFORM_TABLES,
    SPECIAL_RLS_TABLES,
    TENANT_TABLES,
    WORKER_DELETABLE,
)

BACKEND_DIR = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.dbproof

APP_ROLES = ["hms_app", "hms_worker", "hms_platform", "hms_backup"]


def rows(su: psycopg.Connection[Any], query: str, *params: Any) -> list[tuple[Any, ...]]:
    return su.execute(query, params).fetchall()


def test_server_is_postgres_18(su: psycopg.Connection[Any]) -> None:
    (num,) = rows(su, "SHOW server_version_num")[0]
    assert int(num) // 10000 == 18


def test_btree_gist_installed_by_owner_without_superuser(su: psycopg.Connection[Any]) -> None:
    """docs/03 §3 said 'trusted, so hms_owner can create it; to be confirmed'. This confirms it."""
    found = rows(
        su,
        "SELECT r.rolname, r.rolsuper FROM pg_extension e JOIN pg_roles r ON r.oid = e.extowner "
        "WHERE e.extname = 'btree_gist'",
    )
    assert found == [("hms_owner", False)]


def test_migrations_at_head(su: psycopg.Connection[Any]) -> None:
    """The database is at the NEWEST migration file (was: hard-coded "0001_baseline", which
    every later migration would have broken without the schema being wrong)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(Config(str(BACKEND_DIR / "alembic.ini"))).get_current_head()
    assert head is not None
    assert rows(su, "SELECT version_num FROM alembic_version") == [(head,)]


def test_tenant_table_list_matches_database(su: psycopg.Connection[Any]) -> None:
    found = {
        r[0]
        for r in rows(
            su,
            """
        SELECT c.table_name FROM information_schema.columns c
        JOIN information_schema.tables t ON t.table_name = c.table_name AND t.table_schema = c.table_schema
        WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
          AND c.column_name = 'org_id' AND c.is_nullable = 'NO'""",
        )
    }
    assert found - set(SPECIAL_RLS_TABLES) == set(TENANT_TABLES)  # administrator_emails is special (D78)


def test_every_table_is_classified(su: psycopg.Connection[Any]) -> None:
    found = {r[0] for r in rows(su, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
    expected = set(TENANT_TABLES) | set(SPECIAL_RLS_TABLES) | set(PLATFORM_TABLES) | {"alembic_version"}
    assert found == expected, f"unclassified: {found - expected}; missing: {expected - found}"


def test_every_org_table_has_forced_rls_and_policy(su: psycopg.Connection[Any]) -> None:
    problems = rows(
        su,
        """
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
               (SELECT count(*) FROM pg_policies p WHERE p.schemaname = 'public' AND p.tablename = c.relname)
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
          AND (EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'org_id'
                        AND NOT a.attisdropped) OR c.relname = 'organizations')
          AND NOT (c.relrowsecurity AND c.relforcerowsecurity
                   AND EXISTS (SELECT 1 FROM pg_policies p WHERE p.schemaname = 'public'
                                AND p.tablename = c.relname))""",
    )
    assert problems == []


@pytest.mark.parametrize("role", ["hms_app", "hms_worker"])
def test_platform_tables_have_no_app_grants(su: psycopg.Connection[Any], role: str) -> None:
    for table in [*PLATFORM_TABLES, "alembic_version"]:
        for priv in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
            (has,) = rows(su, "SELECT has_table_privilege(%s, %s, %s)", role, f"public.{table}", priv)[0]
            assert not has, f"{role} has {priv} on {table}"


def test_app_roles_are_not_owner_superuser_or_bypassrls(su: psycopg.Connection[Any]) -> None:
    for role in APP_ROLES:
        (sup, bypass) = rows(su, "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s", role)[0]
        assert not sup and not bypass, role
        (owned,) = rows(
            su, "SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner WHERE r.rolname = %s", role
        )[0]
        assert owned == 0, f"{role} owns {owned} relations"
    (owner_bypass,) = rows(su, "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = 'hms_owner'")[0]
    assert not owner_bypass


def test_no_role_can_delete_business_data(su: psycopg.Connection[Any]) -> None:
    for table in TENANT_TABLES + SPECIAL_RLS_TABLES:
        for role in APP_ROLES:
            (has,) = rows(su, "SELECT has_table_privilege(%s, %s, 'DELETE')", role, f"public.{table}")[0]
            allowed = role == "hms_worker" and table in WORKER_DELETABLE
            assert has == allowed, f"{role} DELETE on {table}: {has}"


def test_version_column_on_editable_tables(su: psycopg.Connection[Any]) -> None:
    found = {
        r[0]
        for r in rows(
            su,
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND column_name = 'version' "
            "AND data_type = 'integer' AND is_nullable = 'NO'",
        )
    }
    assert found == set(EDITABLE_TABLES)


def test_bed_states_view_is_security_invoker(su: psycopg.Connection[Any]) -> None:
    (opts,) = rows(su, "SELECT reloptions FROM pg_class WHERE relname = 'bed_states'")[0]
    assert "security_invoker=true" in (opts or [])
