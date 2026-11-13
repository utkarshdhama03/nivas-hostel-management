"""Fixes from the independent review: payment references unique among payments that stand.

Revision ID: 0006_review
Revises: 0005_clock
Create Date: 2026-11-10
"""

from pathlib import Path

from alembic import op

revision = "0006_review"
down_revision = "0005_clock"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[1] / "sql" / "0006_review"


def upgrade() -> None:
    raw = op.get_bind().connection.driver_connection
    for path in sorted(SQL_DIR.glob("*.sql")):
        with raw.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("Forward-only; recreate the development database to go back (README).")
