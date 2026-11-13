"""No two-step verification: TOTP, recovery codes, sign-in codes and the session marks go (D87).

The downgrade recreates the structure (sql/0017_no_two_step/down/); secrets and codes don't come back.

Revision ID: 0017_no_two_step
Revises: 0016_remove_inventory
Create Date: 2026-11-12
"""

from pathlib import Path

from alembic import op

revision = "0017_no_two_step"
down_revision = "0016_remove_inventory"
branch_labels = None
depends_on = None

SQL_DIR = Path(__file__).resolve().parents[1] / "sql" / "0017_no_two_step"


def _run(folder: Path) -> None:
    raw = op.get_bind().connection.driver_connection
    for path in sorted(folder.glob("*.sql")):
        with raw.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))


def upgrade() -> None:
    _run(SQL_DIR)


def downgrade() -> None:
    _run(SQL_DIR / "down")
