"""Health endpoints (docs/04 §12.1).

/health/live  : the process is up. Public, cheap, touches nothing.
/health/ready : the database is reachable AND its schema is at the newest migration
                the code knows about. Answers only to the local machine (loopback),
                because it reveals the schema version.
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.db import transaction
from app.core.errors import ProblemError

router = APIRouter(prefix="/health", tags=["health"])
ALEMBIC_INI = Path(__file__).resolve().parents[3] / "alembic.ini"


@lru_cache(maxsize=1)
def expected_revision() -> str | None:
    return ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_current_head()


def _is_loopback(host: str | None) -> bool:
    try:
        return host is not None and ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request) -> dict[str, str]:
    if not _is_loopback(request.client.host if request.client else None):
        raise ProblemError(404, "not_found", "Not found")
    try:
        with transaction(None) as db:
            current = db.execute(text("SELECT app_schema_version()")).scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise ProblemError(503, "not_ready", "Database unavailable") from exc
    if current != expected_revision():
        raise ProblemError(503, "not_ready", "Database schema is not up to date")
    return {"status": "ready", "schema": str(current)}
