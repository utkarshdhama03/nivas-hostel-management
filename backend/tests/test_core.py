"""Platform core: logging redaction, start-up checks, health, model drift, audit.

docs/05 AUTH-12 (no secrets in logs), MT-11 (refuse to run as an RLS-bypassing role),
OPS-4 (keys checked at start-up), B5 (audit in the same transaction, allowlisted fields).
"""

from __future__ import annotations

import importlib
import json
import logging
import pkgutil
import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import audit, request_state
from app.core.config import ConfigError
from app.core.db import Base, engine, transaction
from app.core.logging import JsonFormatter
from app.core.models import AuditLog
from app.core.permissions import Principal, RoleGrant
from app.core.startup import check_database_role, check_keys
from app.main import create_app
from app.modules.health.router import expected_revision
from tests.support import Org

# --- logging ----------------------------------------------------------------------------------


def _format(msg: str, **extra: Any) -> dict[str, Any]:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, msg, None, None)
    for k, v in extra.items():
        setattr(record, k, v)
    out: dict[str, Any] = json.loads(JsonFormatter().format(record))
    return out


def test_log_redaction() -> None:
    jwt_like = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJlLXZhbHVl"
    out = _format(
        f"Authorization: Bearer abc.def password=hunter2 token={jwt_like}",
        password="hunter2",
        otp="123456",
        refresh_token="r",
        nested={"secret": "s", "ok": "fine"},
    )
    text = json.dumps(out)
    for leaked in ("hunter2", "123456", "abc.def", jwt_like, '"s"'):
        assert leaked not in text, leaked
    assert out["nested"]["ok"] == "fine"


def test_request_log_has_no_headers_or_query(caplog: pytest.LogCaptureFixture, app_db: str) -> None:
    client = TestClient(create_app(run_checks=False))
    with caplog.at_level(logging.INFO, logger="hms.access"):
        client.get("/api/v1/health/live?token=abc123", headers={"Authorization": "Bearer sekret-value"})
    lines = [JsonFormatter().format(r) for r in caplog.records if r.name == "hms.access"]
    assert lines, "access log line expected"
    assert all("abc123" not in line and "sekret-value" not in line for line in lines)
    assert json.loads(lines[-1])["path"] == "/api/v1/health/live"


# --- start-up checks ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["hms_app", "hms_worker", "hms_platform"])
def test_app_roles_pass_startup_check(app_db: str, role: str) -> None:
    check_database_role(role)  # type: ignore[arg-type]


@pytest.mark.parametrize("role", ["hms_owner", "postgres"])
def test_startup_refuses_rls_bypassing_roles(app_db: str, role: str) -> None:
    """MT-11: the owner and the superuser would skip RLS, so the app refuses to run as them."""
    with pytest.raises(ConfigError, match="Refusing to start"):
        check_database_role(role)  # type: ignore[arg-type]
    engine.cache_clear()


def test_keys_checked_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    check_keys()
    monkeypatch.setattr(get_settings(), "hms_jwt_platform_keys", None)
    with pytest.raises(ConfigError, match="HMS_JWT_PLATFORM_KEYS"):
        check_keys()


def test_startup_runs_in_lifespan(app_db: str) -> None:
    with TestClient(create_app()) as client:  # lifespan = run_startup_checks
        assert client.get("/api/v1/health/live").json() == {"status": "ok"}


# --- health ---------------------------------------------------------------------------------------


def test_ready_only_from_loopback(app_db: str) -> None:
    app = create_app(run_checks=False)
    assert TestClient(app).get("/api/v1/health/ready").status_code == 404
    r = TestClient(app, client=("127.0.0.1", 5000)).get("/api/v1/health/ready")
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ready", "schema": expected_revision()}


# --- model drift --------------------------------------------------------------------------------


def _import_all_models() -> None:
    import app.modules

    for mod in pkgutil.walk_packages(app.modules.__path__, "app.modules."):
        if mod.name.endswith(".models"):
            importlib.import_module(mod.name)


def test_models_match_the_database(su: psycopg.Connection[Any], app_db: str) -> None:
    """The SQL migration is the source of truth; every ORM model must describe it exactly:
    same columns, same nullability. Catches a migration change the models missed."""
    _import_all_models()
    problems: list[str] = []
    for table in Base.metadata.sorted_tables:
        rows = su.execute(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table.name,),
        ).fetchall()
        db_cols = {name: nullable == "YES" for name, nullable in rows}
        if not db_cols:
            problems.append(f"{table.name}: table missing in database")
            continue
        model_cols = {c.name: bool(c.nullable) for c in table.columns}
        for missing in sorted(set(db_cols) - set(model_cols)):
            problems.append(f"{table.name}.{missing}: in database, not in model")
        for extra in sorted(set(model_cols) - set(db_cols)):
            problems.append(f"{table.name}.{extra}: in model, not in database")
        for name in sorted(set(db_cols) & set(model_cols)):
            if db_cols[name] != model_cols[name]:
                problems.append(f"{table.name}.{name}: nullable db={db_cols[name]} model={model_cols[name]}")
    assert not problems, "\n".join(problems)
    # Grows as models are added; every one is checked above (the schema cleanup dropped the five tables
    # D75 had left without models).
    assert len(Base.metadata.tables) == 45  # D85: four inventory tables gone; D87: two recovery-code tables


# --- audit -----------------------------------------------------------------------------------------


def _principal(org: Org, key: str) -> Principal:
    p = org.people[key]
    return Principal(
        user_id=p.user_id,
        org_id=p.org_id,
        session_id=p.session_id,
        kind=p.kind,
        grants=tuple(RoleGrant(r, None) for r in p.roles),
    )


def test_audit_row_allowlist_and_request_metadata(org_a: Org) -> None:
    rid = str(uuid.uuid4())
    tokens = (
        request_state.request_id.set(rid),
        request_state.client_ip.set("203.0.113.9"),
        request_state.user_agent.set("pytest-agent"),
    )
    try:
        with transaction(org_a.id) as db:
            row = audit.record(
                db,
                _principal(org_a, "admin"),
                "user.updated",
                "user",
                org_a.people["student"].user_id,
                before={"full_name": "Old", "status": "active", "password_hash": "$argon2id$x", "secret": "s"},
                after={"full_name": "New", "status": "active", "password_hash": "$argon2id$y", "secret": "t"},
            )
            row_id = row.id
    finally:
        request_state.request_id.reset(tokens[0])
        request_state.client_ip.reset(tokens[1])
        request_state.user_agent.reset(tokens[2])

    with transaction(org_a.id) as db:
        saved = db.get(AuditLog, row_id)
        assert saved is not None
        assert saved.org_id == org_a.id and saved.actor_type == "user"
        assert saved.actor_roles == ["super_admin"]
        assert saved.changed_fields == ["full_name"]  # secrets never even appear as names
        assert saved.before_values == {"full_name": "Old", "status": "active"}
        assert saved.after_values == {"full_name": "New", "status": "active"}
        assert str(saved.request_id) == rid and str(saved.ip_address) == "203.0.113.9"
        assert saved.user_agent == "pytest-agent"


def test_audit_rolls_back_with_the_change(org_a: Org) -> None:
    marker = uuid.uuid4()
    with pytest.raises(RuntimeError), transaction(org_a.id) as db:
        audit.record(db, _principal(org_a, "admin"), "user.updated", "user", marker)
        raise RuntimeError("the business change failed")
    with transaction(org_a.id) as db:
        assert db.scalars(select(AuditLog).where(AuditLog.entity_id == marker)).first() is None


def test_system_actor_from_worker(org_a: Org) -> None:
    with transaction(org_a.id, role="hms_worker") as db:
        row = audit.record(db, audit.SYSTEM, "jobs.purged", "job", None, org_id=org_a.id)
        assert row.actor_type == "system" and row.actor_user_id is None


def test_schema_version_function_exposes_only_the_version(connect: Any, app_db: str) -> None:
    """The app reads the version through app_schema_version() but still can't read the table."""
    conn = connect("hms_app")
    assert conn.execute("SELECT app_schema_version()").fetchone() == (expected_revision(),)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT * FROM alembic_version")
