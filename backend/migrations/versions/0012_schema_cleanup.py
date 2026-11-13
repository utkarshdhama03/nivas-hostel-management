"""Drop what D75/D76 left unused: gate passes, attendance, the personal QR, the old roles (approved).

The downgrade recreates the structure (sql/0012_schema_cleanup/down/); deleted rows don't come back.

Revision ID: 0012_schema_cleanup
Revises: 0011_student_requests
Create Date: 2026-11-11
"""

from pathlib import Path

from alembic import op

revision = "0012_schema_cleanup"
down_revision = "0011_student_requests"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[1] / "sql" / "0012_schema_cleanup"


def _run(folder: Path) -> None:
    raw = op.get_bind().connection.driver_connection
    for path in sorted(folder.glob("*.sql")):
        with raw.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))


def upgrade() -> None:
    _run(SQL_DIR)


def downgrade() -> None:
    _run(SQL_DIR / "down")
