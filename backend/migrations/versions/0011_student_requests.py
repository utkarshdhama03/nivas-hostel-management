"""Student registration requests (D74): the table, the organization switch, the verification link.

Revision ID: 0011_student_requests
Revises: 0010_reset_code
Create Date: 2026-11-11
"""

from pathlib import Path

from alembic import op

revision = "0011_student_requests"
down_revision = "0010_reset_code"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[1] / "sql" / "0011_student_requests"


def upgrade() -> None:
    raw = op.get_bind().connection.driver_connection
    for path in sorted(SQL_DIR.glob("*.sql")):
        with raw.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("Forward-only; recreate the development database to go back (README).")
