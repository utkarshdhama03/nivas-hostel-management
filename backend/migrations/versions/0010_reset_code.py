"""Forgot password by an emailed 6-digit code; the reset link is gone (D77).

Revision ID: 0010_reset_code
Revises: 0009_one_administrator
Create Date: 2026-11-11
"""

from pathlib import Path

from alembic import op

revision = "0010_reset_code"
down_revision = "0009_one_administrator"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[1] / "sql" / "0010_reset_code"


def upgrade() -> None:
    raw = op.get_bind().connection.driver_connection
    for path in sorted(SQL_DIR.glob("*.sql")):
        with raw.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("Forward-only; recreate the development database to go back (README).")
