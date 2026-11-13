"""app_now(): an injectable clock for business time only (D67).

Revision ID: 0005_clock
Revises: 0004_scale
Create Date: 2026-11-10
"""

from pathlib import Path

from alembic import op

revision = "0005_clock"
down_revision = "0004_scale"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[1] / "sql" / "0005_clock"


def upgrade() -> None:
    raw = op.get_bind().connection.driver_connection
    for path in sorted(SQL_DIR.glob("*.sql")):
        with raw.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("Forward-only; recreate the development database to go back (README).")
