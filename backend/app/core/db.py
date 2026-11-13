"""Database access: engines per role, and THE way to open a transaction.

Rule (docs/04 §4.1, docs/05 MT-5): no other module creates SQLAlchemy sessions or
engines. Everything goes through `transaction()`, which sets the tenant context
first, so no code path can run a query without it.

How tenant context works:
  BEGIN
  SELECT set_config('app.org_id', '<uuid or empty>', true)   -- true = local to this transaction
  ... the caller's queries: RLS policies compare org_id with app_current_org() ...
  COMMIT / ROLLBACK                                         -- the setting disappears here
Because the setting is transaction-local, a pooled connection can never carry one
organization's context into the next request.

Why set_config() and not "SET LOCAL app.org_id = ...": SET can't take a bind
parameter, so the value would have to be pasted into the SQL text. set_config()
takes it as a parameter: no SQL-injection path, even in principle.

The business clock (D67): the same statement sets app.now to the application's clock, so
app_now() in SQL (business tables, business logic) and datetime.now() in Python agree. In
production both are the real time; tests freeze the Python clock and the database follows.
Security tables (audit, sessions, tokens, MFA, login attempts...) ignore app.now: they use the
real now(), so no application code can backdate them (03 §1, tests/test_clock.py).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import cache
from typing import Any, Literal

from sqlalchemy import Engine, MetaData, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session

from app.core.config import get_settings

AppRole = Literal["hms_app", "hms_worker", "hms_platform"]

# Constraint names follow PostgreSQL's own defaults, so models match the SQL migration.
NAMING = {"ix": "%(table_name)s_%(column_0_name)s_idx"}


class Base(DeclarativeBase):
    """Base class for all ORM models. The schema itself is created by the SQL migration,
    never by SQLAlchemy (no create_all); tests/test_model_drift.py checks the two agree."""

    metadata = MetaData(naming_convention=NAMING)


@cache
def engine(role: AppRole, database: str | None = None) -> Engine:
    """One connection pool per role (and database). pool_pre_ping drops dead connections."""
    settings = get_settings()
    return create_engine(
        settings.db_url(role, database),
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
        pool_reset_on_return="rollback",
    )


@contextmanager
def transaction(
    org_id: uuid.UUID | None,
    role: AppRole = "hms_app",
    database: str | None = None,
) -> Iterator[Session]:
    """Open a session, BEGIN, set the tenant context, yield; COMMIT on success, ROLLBACK on error.

    org_id=None means "no tenant": RLS then returns zero rows on tenant tables (fail closed).
    Used for pre-login lookups (organizations is readable without context) and platform work.
    """
    with Session(engine(role, database), expire_on_commit=False) as session, session.begin():
        session.execute(
            text("SELECT set_config('app.org_id', :org, true), set_config('app.now', :now, true)"),
            {"org": str(org_id) if org_id else "", "now": datetime.now(UTC).isoformat()},
        )
        yield session


def set_tenant(session: Session, org_id: uuid.UUID | None) -> None:
    """Change the tenant for the rest of the current transaction (pre-login flows resolve the
    organization from its slug first, then switch context to it)."""
    session.execute(
        text("SELECT set_config('app.org_id', :org, true)"),
        {"org": str(org_id) if org_id else ""},
    )


def rowcount(result: Any) -> int:
    """How many rows an UPDATE/DELETE touched. (SQLAlchemy's generic Result type doesn't
    declare `rowcount`, so this one typed helper replaces scattered type-ignores.)"""
    return int(result.rowcount)
