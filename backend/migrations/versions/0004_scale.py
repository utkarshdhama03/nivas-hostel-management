"""Index for the organization audit log page order (found by the Phase 10 scale test).

Revision ID: 0004_scale
Revises: 0003_auth
Create Date: 2026-11-09
"""

from pathlib import Path

from alembic import op

revision = "0004_scale"
down_revision = "0003_auth"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[1] / "sql" / "0004_scale"


def upgrade() -> None:
    raw = op.get_bind().connection.driver_connection
    for path in sorted(SQL_DIR.glob("*.sql")):
        with raw.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))


def downgrade() -> None:
    raise NotImplementedError("Forward-only; recreate the development database to go back (README).")
