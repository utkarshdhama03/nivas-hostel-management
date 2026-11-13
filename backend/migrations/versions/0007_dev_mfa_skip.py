"""Development-only MFA skip (D72): sessions opened without the second step are marked.

Revision ID: 0007_dev_mfa_skip
Revises: 0006_review
Create Date: 2026-11-10
"""

from pathlib import Path

from alembic import op

revision = "0007_dev_mfa_skip"
down_revision = "0006_review"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[1] / "sql" / "0007_dev_mfa_skip"


def upgrade() -> None:
    raw = op.get_bind().connection.driver_connection
    for path in sorted(SQL_DIR.glob("*.sql")):
        with raw.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("Forward-only; recreate the development database to go back (README).")
