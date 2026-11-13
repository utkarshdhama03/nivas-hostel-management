"""Security suites GENERATED from the route table (docs/05 §2, §3.1, RBAC-4, RBAC-11).

Adding an endpoint without classifying it fails test_every_route_is_classified; once it is in
SECURITY_MATRIX it automatically joins every suite below.

Expected answers, derived from the permission tables in app/core/permissions.py:
  * a role WITHOUT the route's permission  -> 403 (permission_denied / wrong_portal);
  * a role WITH it, on another organization's record or another hostel's record -> 404;
  * never 2xx, never 500, and the victim organization's data is unchanged.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.core import tokens
from app.core.permissions import ROLE_PERMISSIONS
from app.main import create_app
from tests.security.conftest import SecWorld, people
from tests.security.matrix import PUBLIC_ROUTES, SECURITY_MATRIX, RouteSpec
from tests.support import Person

ORG_ROUTES = sorted(k for k, v in SECURITY_MATRIX.items() if v.auth == "org")
PLATFORM_ROUTES = sorted(k for k, v in SECURITY_MATRIX.items() if v.auth == "platform")
RESOURCE_ROUTES = sorted(k for k, v in SECURITY_MATRIX.items() if v.auth == "org" and v.params)
LIST_ROUTES = sorted(k for k, v in SECURITY_MATRIX.items() if v.auth == "org" and k[0] == "GET" and not v.params)
SELF_ROUTES = sorted(
    k for k in RESOURCE_ROUTES if set(SECURITY_MATRIX[k].params.values()) & {"notification", "session"}
)
DENIED = {"permission_denied", "wrong_portal"}
UNAUTHENTICATED = {"session_invalid", "token_expired"}
CSV = b"building,floor,room,room_type,bed\nZ,1,1,Double,A\n"
ids = lambda k: f"{k[0]} {k[1]}"  # noqa: E731


def _api_routes() -> Iterator[Any]:
    """Every API route with its full path. FastAPI 0.162 keeps included routers as wrappers
    (_IncludedRouter); effective_route_contexts() yields their routes with prefixes applied."""
    for route in create_app(run_checks=False).routes:
        if hasattr(route, "effective_route_contexts"):
            yield from route.effective_route_contexts()
        elif isinstance(route, APIRoute):
            yield route


def _app_routes() -> set[tuple[str, str]]:
    return {(m, r.path) for r in _api_routes() for m in r.methods if m != "HEAD"}


def _required_permissions(route: Any) -> set[str]:
    found: set[str] = set()
    stack = [route.dependant]
    while stack:
        dep = stack.pop()
        perm = getattr(dep.call, "required_permission", None)
        if perm:
            found.add(perm)
        stack.extend(dep.dependencies)
    return found


def test_every_route_is_classified() -> None:
    routes = _app_routes()
    assert len(routes) > 60, "the route walk found too few routes; it is broken"
    classified = set(SECURITY_MATRIX) | set(PUBLIC_ROUTES)
    assert not routes - classified, f"unclassified routes: {sorted(routes - classified)}"
    assert not classified - routes, f"stale entries: {sorted(classified - routes)}"
    assert not set(SECURITY_MATRIX) & set(PUBLIC_ROUTES)


def test_declared_permission_matches_the_code() -> None:
    for route in _api_routes():
        for method in route.methods:
            spec = SECURITY_MATRIX.get((method, route.path))
            if spec is not None:
                expected = {spec.permission} if spec.permission else set()
                assert _required_permissions(route) == expected, (method, route.path)


STUDENT_ID_PARAMS = {"student_id", "user_id"}
PORTAL_ID_ROUTES = sorted(k for k in RESOURCE_ROUTES if k[1].startswith("/api/v1/portal"))


def test_portal_routes_have_no_student_id_params() -> None:
    """RBAC-3 (04 §12.18): portal routes are "me": no student or user ID ever appears in a portal
    URL. A portal route may name one of the student's OWN records (a receipt); the next test proves
    another student's record behind such an ID is a 404."""
    for _, path in _app_routes():
        if path.startswith("/api/v1/portal"):
            assert not set(re.findall(r"\{(\w+)\}", path)) & STUDENT_ID_PARAMS, path


@pytest.mark.parametrize("key", PORTAL_ID_ROUTES, ids=ids)
def test_portal_route_ids_are_only_the_students_own(client: TestClient, world: SecWorld, key: tuple[str, str]) -> None:
    """The world's portal student asks for records of OTHER students of the same organization."""
    spec = SECURITY_MATRIX[key]
    me = world.orgs["a"].people["student"]
    for hostel in ("h1", "h2"):
        values = {param: world.resources["a"][kind][hostel] for param, kind in spec.params.items()}
        r = _call(client, key, me.headers(), values)
        assert r.status_code == 404, r.text


def _url(path: str, values: dict[str, Any]) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), uuid.uuid4())), path)


def _call(
    client: TestClient,
    key: tuple[str, str],
    headers: dict[str, str],
    values: dict[str, Any] | None = None,
    *,
    valid_body: bool = True,
) -> Any:
    method, path = key
    spec = SECURITY_MATRIX[key]
    if spec.multipart:
        return client.request(
            method, _url(path, values or {}), headers=headers, files={"file": ("rooms.csv", CSV, "text/csv")}
        )
    if spec.body and valid_body:
        body: Any = spec.body()
    else:
        body = {} if method in ("POST", "PATCH") else None
    return client.request(method, _url(path, values or {}), headers=headers, json=body)


def _allowed(spec: RouteSpec, p: Person) -> bool:
    if spec.portal and spec.portal != p.kind:
        return False
    if spec.permission is None:
        return True
    return any(spec.permission in ROLE_PERMISSIONS[r] for r in p.roles)


def _code(r: Any) -> str | None:
    return r.json().get("code") if r.headers.get("content-type", "").startswith("application/problem") else None


# --- authentication: every protected route rejects missing and wrong tokens -----------------------


@pytest.mark.parametrize("key", ORG_ROUTES, ids=ids)
def test_org_route_requires_valid_access_token(client: TestClient, world: SecWorld, key: tuple[str, str]) -> None:
    p = world.orgs["a"].people["student"]
    common = {"sub": p.user_id, "sid": p.session_id, "org": p.org_id}
    bad = {
        "missing": {},
        "garbage": {"Authorization": "Bearer nope"},
        "other_type": {"Authorization": f"Bearer {tokens.issue(audience='hms-api', typ='other', **common)}"},  # type: ignore[arg-type]
        "platform_audience": {
            "Authorization": "Bearer " + tokens.issue(audience="hms-platform", typ="access", **common)
        },
        "expired": {
            "Authorization": "Bearer "
            + tokens.issue(audience="hms-api", typ="access", **common, now=datetime.now(UTC) - timedelta(hours=1))
        },
    }
    for name, headers in bad.items():
        r = _call(client, key, headers)
        assert r.status_code == 401, (name, r.status_code, r.text)
        assert _code(r) in UNAUTHENTICATED, name


@pytest.mark.parametrize("key", PLATFORM_ROUTES, ids=ids)
def test_platform_route_rejects_org_tokens(client: TestClient, world: SecWorld, key: tuple[str, str]) -> None:
    for _, p in people(world):
        r = _call(client, key, p.headers(), {"org_id": world.orgs["b"].id})
        assert r.status_code == 401, r.text


# --- cross-tenant (05 §3.1): org B's IDs never work for anyone in org A ------------------------------------


def _snapshot(su: Any, org_id: uuid.UUID) -> dict[str, Any]:
    """The victim's data, read through the superuser connection (bypasses RLS)."""
    out: dict[str, Any] = {}
    for table in (
        "hostels",
        "buildings",
        "floors",
        "rooms",
        "beds",
        "room_types",
        "users",
        "staff_members",
        "role_assignments",
        "sessions",
        "notifications",
        "academic_sessions",
        "academic_terms",
        "auth_tokens",
        "organization_profiles",
        "leave_requests",
        "complaints",
        "complaint_updates",
        "complaint_categories",
        "mess_menus",
        "announcements",
        # Money, students and files (review Should fix 5): the tables an attack would most want to change.
        "students",
        "student_guardians",
        "student_documents",
        "files",
        "bed_allocations",
        "fee_structures",
        "ledger_entries",
        "ledger_allocations",
        "payments",
        "payment_reversals",
        "payment_submissions",
        "refund_requests",
        "complaint_attachments",
    ):
        rows = su.execute(
            f"SELECT row_to_json(t)::text FROM {table} t WHERE org_id = %s ORDER BY 1",  # noqa: S608
            (org_id,),
        ).fetchall()
        out[table] = [r[0] for r in rows]
    return out


@pytest.mark.parametrize("key", RESOURCE_ROUTES, ids=ids)
def test_cross_tenant_resource_returns_404(client: TestClient, world: SecWorld, su: Any, key: tuple[str, str]) -> None:
    spec = SECURITY_MATRIX[key]
    before = _snapshot(su, world.orgs["b"].id)
    victims = {param: world.ids("b", kind)[:3] for param, kind in spec.params.items()}
    for i in range(max(len(v) for v in victims.values())):
        values = {param: ids_[i % len(ids_)] for param, ids_ in victims.items()}
        for attacker_key, attacker in people(world, "a"):
            r = _call(client, key, attacker.headers(), values)
            expected = 404 if _allowed(spec, attacker) else 403
            assert r.status_code == expected, (attacker_key, r.status_code, r.text)
            _no_foreign_data(world, r.text)
    assert _snapshot(su, world.orgs["b"].id) == before, "org B changed"


# RBAC-4 (a role limited to one hostel gets 404 for another hostel's records) no longer applies: since
# D75 there is one role, and an Administrator works in every hostel. Cross-ORGANIZATION access is still
# tested on every route above and below (05 MT-*).


@pytest.mark.parametrize("key", SELF_ROUTES, ids=ids)
def test_other_users_resource_returns_404(client: TestClient, world: SecWorld, key: tuple[str, str]) -> None:
    """Self scope: a colleague in the SAME organization is just as invisible."""
    spec = SECURITY_MATRIX[key]
    attacker = world.orgs["a"].people["student"]
    values = {param: world.resources["a"][kind]["admin"] for param, kind in spec.params.items()}
    r = _call(client, key, attacker.headers(), values)
    assert r.status_code == 404, r.text


@pytest.mark.parametrize("key", LIST_ROUTES, ids=ids)
def test_lists_contain_only_own_data(client: TestClient, world: SecWorld, key: tuple[str, str]) -> None:
    spec = SECURITY_MATRIX[key]
    for person_key, p in people(world, "a"):
        r = _call(client, key, p.headers(), {})
        if not _allowed(spec, p):
            assert r.status_code == 403 and _code(r) in DENIED, (person_key, r.text)
            continue
        assert r.status_code == 200, (person_key, r.text)
        _no_foreign_data(world, r.text)  # MT-8: nothing of org B, ever
        for other_key, nid in world.resources["a"]["notification"].items():
            if other_key != person_key:
                assert str(nid) not in r.text  # nor another user's notifications


def _no_foreign_data(w: SecWorld, text: str) -> None:
    """MT-8. A problem body's `instance` echoes the request path, i.e. the ID the attacker sent
    themselves; that is not leaked data, so it's left out of the scan. Everything else is scanned."""
    try:
        body = json.loads(text)
    except ValueError:
        body = None
    if isinstance(body, dict):
        body.pop("instance", None)
        text = json.dumps(body)
    for value in w.names["b"]:
        assert value not in text, f"org B data leaked: {value}"


# --- permission matrix (05 RBAC) ------------------------------------------------------------------------


@pytest.mark.parametrize("key", [k for k in ORG_ROUTES if SECURITY_MATRIX[k].auth == "org"], ids=ids)
def test_permission_matrix(client: TestClient, world: SecWorld, key: tuple[str, str]) -> None:
    """Every role against every route. Path IDs are random (so nothing real is touched) and create
    bodies are empty (so nothing is created): an allowed role gets past authorization (404/422/...),
    a forbidden role gets 403."""
    spec = SECURITY_MATRIX[key]
    for person_key, p in people(world, "a"):
        r = _call(client, key, p.headers(), {}, valid_body=bool(spec.params))
        code = _code(r)
        assert code not in UNAUTHENTICATED, (person_key, r.text)
        assert r.status_code < 500, (person_key, r.text)
        if _allowed(spec, p):
            assert code not in DENIED, (person_key, r.text)
        else:
            assert r.status_code == 403 and code in DENIED, (person_key, r.text)
