"""Baseline schema: the complete database design from docs/03.

Revision ID: 0001_baseline
Revises: (none)
Create Date: 2026-11-07

Why plain SQL files instead of Alembic's Python operations or autogenerate:
the design relies on PostgreSQL features autogenerate cannot express (RLS
policies, exclusion constraints, column-level grants, constraint triggers,
SECURITY DEFINER functions). Hand-written SQL states them exactly and is easy
to review next to docs/03.

The files run in name order (00_, 10_, ... 60_) on Alembic's own connection,
so they share one transaction: if any statement fails, nothing is applied.
"""

from pathlib import Path

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[1] / "sql" / "0001_baseline"


def upgrade() -> None:
    # Use the raw psycopg connection: with no parameters, psycopg sends the text
    # as-is, so a file may contain many statements, "$$" function bodies and
    # "%" characters (format('%I'), RAISE '... %') without any escaping.
    raw = op.get_bind().connection.driver_connection
    for path in sorted(SQL_DIR.glob("*.sql")):
        with raw.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError(
        "The baseline cannot be downgraded. To start over, drop and recreate the database "
        "(see README: 'Reset the development database')."
    )
