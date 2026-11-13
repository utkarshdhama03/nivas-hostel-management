"""Shared test fixtures.

The database proof suite needs a real PostgreSQL 18 (the `db` service). Run with:
    docker compose run --rm tools pytest

Session start: drop and recreate the test database (as the postgres superuser),
then apply all migrations as hms_owner. So every run also proves that the schema
builds from an empty database.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
import time_machine
from alembic import command
from alembic.config import Config
from psycopg import sql

from app.core.config import Settings, get_settings
from tests.dbproof.world import World, seed_org
from tests.support import TEST_NOW

BACKEND_DIR = Path(__file__).resolve().parents[1]

# --- The test clock (D67): see TEST_NOW in tests/support.py ----------------------------------------


@pytest.fixture(scope="session", autouse=True)
def fixed_clock() -> Iterator[None]:
    with time_machine.travel(TEST_NOW, tick=True):
        yield


Connect = Callable[..., psycopg.Connection[Any]]


@pytest.fixture(scope="session")
def settings() -> Settings:
    return get_settings()


@pytest.fixture(scope="session")
def test_db(settings: Settings) -> str:
    name = settings.hms_test_db_name
    with psycopg.connect(settings.conninfo("postgres", "postgres"), autocommit=True) as su:
        su.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        su.execute(sql.SQL("CREATE DATABASE {} OWNER hms_owner").format(sql.Identifier(name)))
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.attributes["database"] = name
    command.upgrade(cfg, "head")
    return name


def _opener(settings: Settings, database: str, opened: list[psycopg.Connection[Any]]) -> Connect:
    def _connect(role: str = "hms_app", autocommit: bool = False) -> psycopg.Connection[Any]:
        conn = psycopg.connect(settings.conninfo(role, database), autocommit=autocommit)
        opened.append(conn)
        return conn

    return _connect


@pytest.fixture
def connect(settings: Settings, test_db: str) -> Iterator[Connect]:
    """connect(role="hms_app") -> a new connection to the test database as that role.

    Function-scoped: every connection a test opens is closed when that test ends, so the
    suite never runs PostgreSQL out of connection slots (max_connections = 100).
    """
    opened: list[psycopg.Connection[Any]] = []
    yield _opener(settings, test_db, opened)
    for conn in opened:
        conn.close()


@pytest.fixture(scope="session")
def su(settings: Settings, test_db: str) -> Iterator[psycopg.Connection[Any]]:
    """Superuser connection, autocommit. Bypasses RLS: used ONLY to inspect, never to set up data."""
    conn = psycopg.connect(settings.conninfo("postgres", test_db), autocommit=True)
    yield conn
    conn.close()


def _seed_once(settings: Settings, test_db: str, slug: str) -> World:
    with psycopg.connect(settings.conninfo("hms_app", test_db)) as conn:
        return seed_org(conn, slug)


@pytest.fixture(scope="session")
def world_a(settings: Settings, test_db: str) -> World:
    return _seed_once(settings, test_db, "org-alpha")


@pytest.fixture(scope="session")
def world_b(settings: Settings, test_db: str) -> World:
    return _seed_once(settings, test_db, "org-beta")


@pytest.fixture
def fresh(connect: Connect) -> World:
    """A brand-new organization for tests that change data."""
    return seed_org(connect("hms_app"), f"t-{uuid.uuid4().hex[:12]}")


@pytest.fixture
def app_conn(connect: Connect) -> psycopg.Connection[Any]:
    return connect("hms_app")


# --- Phase 1: the application, pointed at the test database ---------------------------------


@pytest.fixture(scope="session")
def app_db(settings: Settings, test_db: str) -> Iterator[str]:
    """Make app code (db.engine, db.transaction) use the test database instead of "hms"."""
    import tempfile

    from app.core.db import engine
    from app.core.storage import get_storage

    settings.hms_db_name = test_db
    settings.hms_upload_dir = tempfile.mkdtemp(prefix="hms-test-uploads-")  # never the real uploads volume
    engine.cache_clear()
    get_storage.cache_clear()
    yield test_db
    engine.cache_clear()


@pytest.fixture(scope="session")
def org_a(app_db: str) -> Any:
    from tests.support import add_person, make_org

    org = make_org()
    add_person(org, "admin", roles=("super_admin",))
    add_person(org, "student", kind="student")
    return org


@pytest.fixture(scope="session")
def org_b(app_db: str) -> Any:
    from tests.support import add_person, make_org

    org = make_org()
    add_person(org, "admin", roles=("super_admin",))
    add_person(org, "student", kind="student")
    return org


@pytest.fixture(autouse=True)
def _reset_rate_limits() -> Iterator[None]:
    """The in-process limiters are global; every test starts with a clean slate."""
    from app.core import ratelimit

    ratelimit.reset_all()
    yield
