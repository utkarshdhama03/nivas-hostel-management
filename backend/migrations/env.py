"""Alembic environment: how migrations connect to the database.

Always as hms_owner (it owns every object). The target database is "hms"
unless overridden with `-x db=<name>` on the command line, or by a caller
setting config.attributes["database"] (the test suite does this for hms_test).

No `target_metadata`: the schema is defined in hand-written SQL files (see
versions/0001_baseline.py for why), not generated from SQLAlchemy models.
"""

from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import get_settings

config = context.config


def _database_name() -> str:
    return (
        config.attributes.get("database")
        or context.get_x_argument(as_dictionary=True).get("db")
        or get_settings().hms_db_name
    )


def run_migrations_online() -> None:
    url = get_settings().db_url("hms_owner", _database_name())
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        # transaction_per_migration: each migration commits on its own; a failing
        # migration rolls back completely (PostgreSQL has transactional DDL).
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("Offline (SQL script) mode is not used in this project.")
run_migrations_online()
