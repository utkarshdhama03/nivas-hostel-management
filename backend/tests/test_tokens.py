"""JWT issuing and verification (docs/04 §5.6, docs/05 AUTH-*)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest

from app.core import tokens

SUB, SID, ORG = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def org_token(**kw: Any) -> str:
    return tokens.issue(audience="hms-api", typ="access", sub=SUB, sid=SID, org=ORG, **kw)


def test_round_trip_and_no_personal_data() -> None:
    t = org_token()
    claims = tokens.verify(t, audience="hms-api", typ="access")
    assert claims["sub"] == str(SUB) and claims["org"] == str(ORG)
    assert set(claims) == {"iss", "aud", "typ", "sub", "sid", "org", "iat", "exp", "jti"}
    assert jwt.get_unverified_header(t)["kid"] == "2026-11-a"


def test_audiences_use_different_keys() -> None:
    with pytest.raises(tokens.TokenError):
        tokens.verify(org_token(), audience="hms-platform", typ="access")
    plat = tokens.issue(audience="hms-platform", typ="access", sub=uuid.uuid4(), sid=uuid.uuid4(), org=None)
    with pytest.raises(tokens.TokenError):
        tokens.verify(plat, audience="hms-api", typ="access")


def test_type_is_checked() -> None:
    other = tokens.issue(audience="hms-api", typ="other", sub=SUB, sid=SID, org=ORG)  # type: ignore[arg-type]
    with pytest.raises(tokens.TokenError, match="type"):
        tokens.verify(other, audience="hms-api", typ="access")


def test_expiry_and_leeway() -> None:
    with pytest.raises(tokens.TokenExpired):
        tokens.verify(org_token(now=datetime.now(UTC) - timedelta(minutes=16)), audience="hms-api", typ="access")
    edge = org_token(now=datetime.now(UTC) - timedelta(minutes=15, seconds=5))
    tokens.verify(edge, audience="hms-api", typ="access")  # within the 10 s leeway


def _forge(alg: str, key: Any, kid: str = "2026-11-a", **changes: Any) -> str:
    claims = jwt.decode(org_token(), options={"verify_signature": False})
    claims.update(changes)
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": kid})


FORGERIES: dict[str, Callable[[], str]] = {
    "alg-none": lambda: _forge("none", None),
    "wrong-key": lambda: _forge("HS256", b"k" * 32),
    "unknown-kid": lambda: _forge("HS256", b"k" * 32, kid="unknown"),
    "other-alg": lambda: _forge("HS512", b"k" * 64),
}


@pytest.mark.parametrize("name", list(FORGERIES))
def test_forgeries_rejected(name: str) -> None:
    with pytest.raises(tokens.TokenError):
        tokens.verify(FORGERIES[name](), audience="hms-api", typ="access")


def test_tampered_payload_rejected() -> None:
    header, _, sig = org_token().split(".")
    other = tokens.issue(audience="hms-api", typ="access", sub=uuid.uuid4(), sid=SID, org=ORG)
    with pytest.raises(tokens.TokenError):
        tokens.verify(f"{header}.{other.split('.')[1]}.{sig}", audience="hms-api", typ="access")


def test_one_time_secrets() -> None:
    a, b = tokens.new_secret(), tokens.new_secret()
    assert a != b and len(a) >= 43
    assert tokens.hash_secret(a) == tokens.hash_secret(a) != tokens.hash_secret(b)
    assert tokens.secrets_equal(a, a) and not tokens.secrets_equal(a, b)
