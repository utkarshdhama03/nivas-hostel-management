"""Small helpers shared by the proof tests.

- ins():        INSERT a row from keyword arguments, return one column (default "id").
- tenant_tx():  a transaction with app.org_id set, the same thing the API does per request.
- expect_db_error(): assert that a block fails with a given SQLSTATE (and constraint).
- race():       run two functions at the same instant on separate connections.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

# SQLSTATE codes used by the proof suite (PostgreSQL "Appendix A. Error Codes").
INSUFFICIENT_PRIVILEGE = "42501"  # also raised for "new row violates row-level security policy"
FOREIGN_KEY_VIOLATION = "23503"
UNIQUE_VIOLATION = "23505"
CHECK_VIOLATION = "23514"
EXCLUSION_VIOLATION = "23P01"
RESTRICT_VIOLATION = "23001"  # raised by forbid_change() and the "once only" triggers
RAISE_EXCEPTION = "P0001"  # plain RAISE EXCEPTION (organizations status guard)


def ins(cur: psycopg.Cursor[Any], table: str, returning: str = "id", **cols: Any) -> Any:
    """INSERT INTO table (cols...) VALUES (...) RETURNING <returning>; identifiers safely quoted."""
    names = list(cols)
    values = [Jsonb(v) if isinstance(v, dict) else v for v in cols.values()]
    query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING {}").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, names)),
        sql.SQL(", ").join(sql.Placeholder() * len(names)),
        sql.Identifier(returning),
    )
    cur.execute(query, values)
    row = cur.fetchone()
    assert row is not None
    return row[0]


def set_tenant(conn: psycopg.Connection[Any], org_id: uuid.UUID | None) -> None:
    """What the API does at the start of every request's transaction (docs/04 §4.1, app/core/db.py):
    the tenant, and the business clock app.now from the (test-frozen) application clock (D67)."""
    conn.execute(
        "SELECT set_config('app.org_id', %s, true), set_config('app.now', %s, true)",
        (str(org_id) if org_id else "", datetime.now(UTC).isoformat()),
    )


@contextmanager
def tenant_tx(conn: psycopg.Connection[Any], org_id: uuid.UUID | None) -> Iterator[psycopg.Cursor[Any]]:
    """One transaction with the tenant set; commits on success, rolls back on error."""
    try:
        set_tenant(conn, org_id)
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


@contextmanager
def expect_db_error(sqlstate: str, constraint: str | None = None) -> Iterator[None]:
    """The block must raise a database error with this SQLSTATE (and constraint name, if given)."""
    try:
        yield
    except psycopg.Error as exc:
        assert exc.sqlstate == sqlstate, f"expected SQLSTATE {sqlstate}, got {exc.sqlstate}: {exc}"
        if constraint is not None:
            got = exc.diag.constraint_name
            assert got == constraint, f"expected constraint {constraint!r}, got {got!r}: {exc}"
        return
    raise AssertionError(f"expected a database error with SQLSTATE {sqlstate}, but the block succeeded")


@dataclass
class RaceResult:
    ok: list[Any]
    errors: list[psycopg.Error]


def race(
    connect: Callable[[], psycopg.Connection[Any]],
    org_id: uuid.UUID,
    first: Callable[[psycopg.Cursor[Any]], Any],
    second: Callable[[psycopg.Cursor[Any]], Any],
    commit: bool = True,
) -> RaceResult:
    """Run `first` and `second` at the same moment, each in its own transaction and connection.

    A Barrier releases both threads together, so both transactions really overlap: the
    situation where application-level "check, then write" logic fails and only the
    database can decide.
    """
    barrier = threading.Barrier(2)
    ok: list[Any] = []
    errors: list[psycopg.Error] = []
    lock = threading.Lock()

    def run(fn: Callable[[psycopg.Cursor[Any]], Any]) -> None:
        conn = connect()
        try:
            set_tenant(conn, org_id)
            with conn.cursor() as cur:
                barrier.wait(timeout=10)
                result = fn(cur)
            if commit:
                conn.commit()
            with lock:
                ok.append(result)
        except psycopg.Error as exc:
            conn.rollback()
            with lock:
                errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=run, args=(fn,)) for fn in (first, second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return RaceResult(ok=ok, errors=errors)
