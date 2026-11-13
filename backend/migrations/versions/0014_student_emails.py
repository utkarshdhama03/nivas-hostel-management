"""Student sign-in by email only: every student has an email, unique platform-wide; a narrow lookup (D80).

Revision ID: 0014_student_emails
Revises: 0013_administrator_emails
Create Date: 2026-11-12
"""

from pathlib import Path

from alembic import op

revision = "0014_student_emails"
down_revision = "0013_administrator_emails"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[1] / "sql" / "0014_student_emails"


def upgrade() -> None:
    raw = op.get_bind().connection.driver_connection
    for path in sorted(SQL_DIR.glob("*.sql")):
        with raw.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("Forward-only; recreate the development database to go back (README).")
