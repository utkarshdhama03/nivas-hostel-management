"""Every notification opens a page that exists: no 'allocation' category, a known-pages list for links (D84).

Revision ID: 0015_notification_links
Revises: 0014_student_emails
Create Date: 2026-11-12
"""

from pathlib import Path

from alembic import op

revision = "0015_notification_links"
down_revision = "0014_student_emails"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[1] / "sql" / "0015_notification_links"


def upgrade() -> None:
    raw = op.get_bind().connection.driver_connection
    for path in sorted(SQL_DIR.glob("*.sql")):
        with raw.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("Forward-only; recreate the development database to go back (README).")
