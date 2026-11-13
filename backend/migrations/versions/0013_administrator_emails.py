"""Organization sign-in by email only: Administrator emails unique platform-wide, and a narrow lookup (D78).

Revision ID: 0013_administrator_emails
Revises: 0012_schema_cleanup
Create Date: 2026-11-11
"""

from pathlib import Path

from alembic import op

revision = "0013_administrator_emails"
down_revision = "0012_schema_cleanup"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[1] / "sql" / "0013_administrator_emails"


def upgrade() -> None:
    raw = op.get_bind().connection.driver_connection
    for path in sorted(SQL_DIR.glob("*.sql")):
        with raw.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("Forward-only; recreate the development database to go back (README).")
