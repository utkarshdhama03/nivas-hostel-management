"""The request pipeline end to end (docs/04 §4.1, §7; docs/05 MT-4, MT-5, MT-9, WEB-2..5).

A small router of TEST-ONLY routes is mounted on a fresh app. Each route exercises
one part of the pipeline through the real dependency (`Ctx`), real tokens and the
real test database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import jwt
import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update

from app.core import tokens
from app.core.context import Ctx, OrgContext, require
from app.core.db import transaction
from app.core.errors import ProblemError
from app.core.permissions import AUDIT_READ
from app.main import create_app
from app.modules.identity.models import User, UserSession
from app.modules.notifications.models import Notification
from tests.support import Org, add_person

test_router = APIRouter(prefix="/api/v1/_test")


@test_router.get("/whoami")
def whoami(ctx: Ctx) -> dict[str, Any]:
    setting = ctx.db.execute(text("SELECT current_setting('app.org_id', true)")).scalar()
    visible_orgs = ctx.db.execute(text("SELECT count(DISTINCT org_id) FROM users")).scalar()
    return {
        "org_setting": setting,
        "org_id": str(ctx.principal.org_id),
        "user_id": str(ctx.principal.user_id),
        "roles": sorted(ctx.principal.roles),
        "visible_orgs": visible_orgs,
    }


@test_router.post("/whoami")
def whoami_post(ctx: Ctx) -> dict[str, str]:
    return {"ok": "yes"}


@test_router.get("/audit-only")
def audit_only(ctx: Annotated[OrgContext, Depends(require(AUDIT_READ))]) -> dict[str, str]:
    return {"ok": "yes"}


@test_router.post("/notify-self")
def notify_self(ctx: Ctx, fail: bool = False) -> dict[str, str]:
    n = Notification(
        org_id=ctx.principal.org_id,
        user_id=ctx.principal.user_id,
        category="security",
        title="pipeline test",
        body="body",
    )
    ctx.db.add(n)
    ctx.db.flush()
    if fail:
        raise ProblemError(409, "conflict", "Rolled back on purpose", extra={"notification_id": str(n.id)})
    return {"id": str(n.id)}


@test_router.post("/deadlock")
def deadlock(ctx: Ctx) -> None:
    ctx.db.execute(text("DO $$ BEGIN RAISE EXCEPTION 'simulated' USING ERRCODE = '40P01'; END $$"))


@test_router.post("/serialization")
def serialization(ctx: Ctx) -> None:
    ctx.db.execute(text("DO $$ BEGIN RAISE EXCEPTION 'simulated' USING ERRCODE = '40001'; END $$"))


@test_router.post("/fails-at-commit")
def fails_at_commit(ctx: Ctx) -> dict[str, str]:
    """A DEFERRED unique constraint is only checked at COMMIT, i.e. after this function returns.
    If commit ran after the response (FastAPI's default dependency scope), the client would
    get this 200 even though nothing was saved."""
    ctx.db.execute(text("CREATE TEMP TABLE deferred_probe (x int UNIQUE DEFERRABLE INITIALLY DEFERRED) ON COMMIT DROP"))
    ctx.db.execute(text("INSERT INTO deferred_probe VALUES (1), (1)"))
    return {"status": "looks fine so far"}


@test_router.post("/stale-edit")
def stale_edit(ctx: Ctx) -> None:
    user = ctx.db.scalars(select(User).where(User.id == ctx.principal.user_id)).one()
    # Someone else saves the same user in between (their own transaction, committed).
    with transaction(ctx.principal.org_id) as other:
        other.execute(update(User).where(User.id == user.id).values(version=User.version + 1))
    user.phone = "+91 98765 43210"
    ctx.db.flush()  # UPDATE ... WHERE id = :id AND version = :old  -> 0 rows -> StaleDataError


@test_router.get("/boom")
def boom(ctx: Ctx) -> None:
    raise RuntimeError("secret internal detail: SELECT * FROM users password=hunter2")


@pytest.fixture(scope="module")
def client(app_db: str) -> TestClient:
    app: FastAPI = create_app(run_checks=False)
    app.include_router(test_router)
    return TestClient(app)


def _problem(r: Any, status: int, code: str) -> dict[str, Any]:
    assert r.status_code == status, r.text
    assert r.headers["content-type"].startswith("application/problem+json")
    body: dict[str, Any] = r.json()
    assert body["code"] == code
    assert body["request_id"] == r.headers["x-request-id"]
    return body


# --- token checks -------------------------------------------------------------------------


def test_no_token_is_401_with_headers(client: TestClient) -> None:
    r = client.get("/api/v1/_test/whoami")
    _problem(r, 401, "session_invalid")
    assert r.headers["www-authenticate"] == "Bearer"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    uuid.UUID(r.headers["x-request-id"])


def test_valid_token_sets_tenant_context(client: TestClient, org_a: Org, org_b: Org) -> None:
    admin = org_a.people["admin"]
    body = client.get("/api/v1/_test/whoami", headers=admin.headers()).json()
    assert body["org_setting"] == str(org_a.id) == body["org_id"]
    assert body["user_id"] == str(admin.user_id)
    assert body["roles"] == ["super_admin"]
    assert body["visible_orgs"] == 1  # RLS: only org A's users exist in this transaction


def test_client_supplied_org_is_ignored(client: TestClient, org_a: Org, org_b: Org) -> None:
    """MT-4: nothing but the verified token decides the tenant."""
    r = client.get(
        f"/api/v1/_test/whoami?org_id={org_b.id}",
        headers={**org_a.people["admin"].headers(), "X-Org-Id": str(org_b.id)},
    )
    assert r.json()["org_setting"] == str(org_a.id)


def test_expired_token(client: TestClient, org_a: Org) -> None:
    p = org_a.people["student"]
    old = tokens.issue(
        audience="hms-api",
        typ="access",
        sub=p.user_id,
        sid=p.session_id,
        org=p.org_id,
        now=datetime.now(UTC) - timedelta(hours=1),
    )
    _problem(client.get("/api/v1/_test/whoami", headers={"Authorization": f"Bearer {old}"}), 401, "token_expired")


@pytest.mark.parametrize("variant", ["platform_audience", "other_type", "alg_none", "wrong_key", "garbage", "basic"])
def test_bad_tokens_rejected(client: TestClient, org_a: Org, variant: str) -> None:
    p = org_a.people["student"]
    common = {"sub": p.user_id, "sid": p.session_id, "org": p.org_id}
    if variant == "platform_audience":
        header = f"Bearer {tokens.issue(audience='hms-platform', typ='access', **common)}"
    elif variant == "other_type":
        header = f"Bearer {tokens.issue(audience='hms-api', typ='other', **common)}"  # type: ignore[arg-type]
    elif variant == "alg_none":
        claims = jwt.decode(p.token(), options={"verify_signature": False})
        header = "Bearer " + jwt.encode(claims, None, algorithm="none", headers={"kid": "2026-11-a"})  # type: ignore[arg-type]
    elif variant == "wrong_key":
        claims = jwt.decode(p.token(), options={"verify_signature": False})
        header = "Bearer " + jwt.encode(claims, b"x" * 32, algorithm="HS256", headers={"kid": "2026-11-a"})
    elif variant == "garbage":
        header = "Bearer not-a-token"
    else:
        header = "Basic dXNlcjpwYXNz"
    _problem(client.get("/api/v1/_test/whoami", headers={"Authorization": header}), 401, "session_invalid")


def test_token_with_another_orgs_claim_finds_nothing(client: TestClient, org_a: Org, org_b: Org) -> None:
    """Even a correctly signed token naming org B with org A's session fails: under org B's
    tenant context the session row doesn't exist (RLS), so the request is unauthenticated."""
    p = org_a.people["student"]
    forged = tokens.issue(audience="hms-api", typ="access", sub=p.user_id, sid=p.session_id, org=org_b.id)
    _problem(client.get("/api/v1/_test/whoami", headers={"Authorization": f"Bearer {forged}"}), 401, "session_invalid")


# --- session, user and organization state ------------------------------------------------------


def test_revoked_session_stops_working_immediately(client: TestClient, org_a: Org) -> None:
    p = add_person(org_a, "revokee", kind="student")
    assert client.get("/api/v1/_test/whoami", headers=p.headers()).status_code == 200
    with transaction(org_a.id) as db:
        db.execute(
            update(UserSession)
            .where(UserSession.id == p.session_id)
            .values(revoked_at=datetime.now(UTC), revoke_reason="logout")
        )
    _problem(client.get("/api/v1/_test/whoami", headers=p.headers()), 401, "session_invalid")


def test_deactivated_user(client: TestClient, org_a: Org) -> None:
    p = add_person(org_a, "leaver", status="deactivated")  # a closed staff account
    _problem(client.get("/api/v1/_test/whoami", headers=p.headers()), 403, "account_deactivated")


def test_read_only_user_may_only_read(client: TestClient, org_a: Org) -> None:
    p = add_person(org_a, "exited", kind="student", status="read_only")
    assert client.get("/api/v1/_test/whoami", headers=p.headers()).status_code == 200
    _problem(client.post("/api/v1/_test/whoami", headers=p.headers()), 403, "account_read_only")


def test_suspended_organization(client: TestClient, app_db: str) -> None:
    from tests.support import make_org

    org = make_org()
    p = add_person(org, "admin", roles=("super_admin",))
    assert client.get("/api/v1/_test/whoami", headers=p.headers()).status_code == 200
    with transaction(None, role="hms_platform") as db:  # only the Platform Owner may suspend (Q14)
        db.execute(
            text(
                "UPDATE organizations SET status = 'suspended', suspended_at = now(), "
                "suspension_reason = 'test' WHERE id = :id"
            ),
            {"id": org.id},
        )
    _problem(client.get("/api/v1/_test/whoami", headers=p.headers()), 403, "organization_suspended")


# --- permissions --------------------------------------------------------------------------------


def test_route_permission(client: TestClient, org_a: Org) -> None:
    assert client.get("/api/v1/_test/audit-only", headers=org_a.people["admin"].headers()).status_code == 200
    _problem(
        client.get("/api/v1/_test/audit-only", headers=org_a.people["student"].headers()), 403, "permission_denied"
    )


# --- transactions -------------------------------------------------------------------------------


def _notification_exists(org: Org, notification_id: str) -> bool:
    with transaction(org.id) as db:
        return db.get(Notification, uuid.UUID(notification_id)) is not None


def test_success_commits(client: TestClient, org_a: Org) -> None:
    r = client.post("/api/v1/_test/notify-self", headers=org_a.people["student"].headers())
    assert r.status_code == 200
    assert _notification_exists(org_a, r.json()["id"])


def test_error_rolls_back(client: TestClient, org_a: Org) -> None:
    r = client.post("/api/v1/_test/notify-self?fail=true", headers=org_a.people["student"].headers())
    body = _problem(r, 409, "conflict")
    assert not _notification_exists(org_a, body["notification_id"])


def test_commit_time_error_becomes_the_response(client: TestClient, org_a: Org) -> None:
    """The proof for Depends(scope="function"): the COMMIT runs before the response is sent,
    so its failure is what the client sees, never a 200."""
    r = client.post("/api/v1/_test/fails-at-commit", headers=org_a.people["student"].headers())
    _problem(r, 409, "conflict")


@pytest.mark.parametrize("route", ["deadlock", "serialization"])
def test_deadlock_and_serialization_are_retryable_409(client: TestClient, org_a: Org, route: str) -> None:
    """03 §8: 40P01 and 40001 become 409 retry_conflict, never a 500."""
    r = client.post(f"/api/v1/_test/{route}", headers=org_a.people["student"].headers())
    body = _problem(r, 409, "retry_conflict")
    assert body["retryable"] is True
    assert r.headers["retry-after"] == "1"


def test_stale_edit_is_409_edit_conflict(client: TestClient, org_a: Org) -> None:
    p = add_person(org_a, "editor", kind="student")
    _problem(client.post("/api/v1/_test/stale-edit", headers=p.headers()), 409, "edit_conflict")


def test_unhandled_error_is_generic_500(client: TestClient, org_a: Org) -> None:
    """WEB-5: no SQL, stack trace or message in the body; still has headers and request ID."""
    r = client.get("/api/v1/_test/boom", headers=org_a.people["student"].headers())
    body = _problem(r, 500, "internal_error")
    assert "hunter2" not in r.text and "SELECT" not in r.text and "RuntimeError" not in r.text
    assert set(body) <= {"type", "title", "status", "code", "instance", "request_id", "errors", "detail"}
    assert r.headers["x-content-type-options"] == "nosniff"


def test_unknown_route_and_validation_errors_use_problem_format(client: TestClient, org_a: Org) -> None:
    _problem(client.get("/api/v1/nope"), 404, "not_found")
    r = client.get("/api/v1/me/notifications?limit=0", headers=org_a.people["student"].headers())
    body = _problem(r, 422, "validation_error")
    assert body["errors"][0]["field"] == "limit"


def test_every_request_gets_its_own_request_id(client: TestClient) -> None:
    ids = {client.get("/api/v1/health/live").headers["x-request-id"] for _ in range(3)}
    assert len(ids) == 3
    r = client.get("/api/v1/health/live", headers={"X-Request-ID": "attacker-chosen"})
    assert r.headers["x-request-id"] != "attacker-chosen"
