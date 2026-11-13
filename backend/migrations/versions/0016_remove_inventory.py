"""Remove inventory and assets: the four tables and the stock trigger function (D85).

The downgrade recreates the structure (sql/0016_remove_inventory/down/); deleted rows don't come back.

Revision ID: 0016_remove_inventory
Revises: 0015_notification_links
Create Date: 2026-11-12
"""

from pathlib import Path

from alembic import op

revision = "0016_remove_inventory"
down_revision = "0015_notification_links"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[1] / "sql" / "0016_remove_inventory"


def _run(folder: Path) -> None:
    raw = op.get_bind().connection.driver_connection
    for path in sorted(folder.glob("*.sql")):
        with raw.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))


def upgrade() -> None:
    _run(SQL_DIR)


def downgrade() -> None:
    _run(SQL_DIR / "down")
