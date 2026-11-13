"""app_schema_version(): the schema version for the readiness check, without table access.

Revision ID: 0002_readiness
Revises: 0001_baseline
Create Date: 2026-11-07
"""

from pathlib import Path

from alembic import op

revision = "0002_readiness"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[1] / "sql" / "0002_readiness"


def upgrade() -> None:
    raw = op.get_bind().connection.driver_connection
    for path in sorted(SQL_DIR.glob("*.sql")):
        with raw.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP FUNCTION app_schema_version()")
