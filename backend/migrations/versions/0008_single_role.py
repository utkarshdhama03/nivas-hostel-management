"""One organization role, Administrator (D75): old roles revoked, sole-Administrator refunds.

Revision ID: 0008_single_role
Revises: 0007_dev_mfa_skip
Create Date: 2026-11-10
"""

from pathlib import Path

from alembic import op

revision = "0008_single_role"
down_revision = "0007_dev_mfa_skip"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[1] / "sql" / "0008_single_role"


def upgrade() -> None:
    raw = op.get_bind().connection.driver_connection
    for path in sorted(SQL_DIR.glob("*.sql")):
        with raw.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("Forward-only; recreate the development database to go back (README).")
