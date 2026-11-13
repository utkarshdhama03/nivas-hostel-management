"""Exactly one Administrator per organization (D76): extra grants revoked, a second one impossible.

Revision ID: 0009_one_administrator
Revises: 0008_single_role
Create Date: 2026-11-11
"""

from pathlib import Path

from alembic import op

revision = "0009_one_administrator"
down_revision = "0008_single_role"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[1] / "sql" / "0009_one_administrator"


def upgrade() -> None:
    raw = op.get_bind().connection.driver_connection
    for path in sorted(SQL_DIR.glob("*.sql")):
        with raw.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("Forward-only; recreate the development database to go back (README).")
