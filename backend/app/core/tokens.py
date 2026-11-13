"""Signed tokens (docs/04 §5.6) and random one-time secrets.

JWT (JSON Web Token) = header.payload.signature, base64url-encoded. The server
signs it with a secret key (HS256 = HMAC-SHA256); anyone changing the payload
breaks the signature. We pin the algorithm and check every claim:

    iss  "hms"                              who issued it
    aud  "hms-api" | "hms-platform"         which API it is for: an organization token
                                            is useless on platform routes and vice versa,
                                            and the two use different signing keys
    typ  "access"
    sub  user id     org  organization id   sid  session id
    iat / exp        issued at / expiry     jti  unique id
    kid (header)     which key signed it, so keys can be rotated

Deliberately NOT in tokens: roles, names, emails (tokens can end up in logs, and
roles must take effect immediately, so they are loaded per request).

One-time secrets (refresh tokens, activation/reset links) are random 256-bit
values; only their SHA-256 hash is stored. A plain hash is enough here because
the secret itself is high-entropy (unlike a password).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt

from app.core.config import KeySet, get_settings

ISSUER = "hms"
ALGORITHM = "HS256"
Audience = Literal["hms-api", "hms-platform"]
TokenType = Literal["access"]

ACCESS_TTL = timedelta(minutes=15)  # Q67
LEEWAY = timedelta(seconds=10)  # small clock-skew tolerance


class TokenError(Exception):
    """Any reason a token is not acceptable. Callers answer 401 without saying which."""


class TokenExpired(TokenError):
    """Signature and claims were fine but the token is too old. The only reason the client is
    told about (401 token_expired), because it means "refresh and retry", not "log in again"."""


def _keys(audience: Audience) -> KeySet:
    return get_settings().jwt_keys(audience)


def issue(
    *,
    audience: Audience,
    typ: TokenType,
    sub: uuid.UUID,
    sid: uuid.UUID,
    org: uuid.UUID | None,
    ttl: timedelta | None = None,
    extra: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> str:
    keys = _keys(audience)
    issued = now or datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": audience,
        "typ": typ,
        "sub": str(sub),
        "sid": str(sid),
        "org": str(org) if org else None,
        "iat": issued,
        "exp": issued + (ttl or ACCESS_TTL),
        "jti": secrets.token_hex(16),
        **(extra or {}),
    }
    return jwt.encode(claims, keys.active_key(), algorithm=ALGORITHM, headers={"kid": keys.active})


def verify(token: str, *, audience: Audience, typ: TokenType) -> dict[str, Any]:
    """Return the claims, or raise TokenError. Only HS256, only our issuer and audience."""
    keys = _keys(audience)
    try:
        header = jwt.get_unverified_header(token)
        key = keys.keys.get(header.get("kid", ""))
        if key is None or header.get("alg") != ALGORITHM:
            raise TokenError("unknown key or algorithm")
        claims: dict[str, Any] = jwt.decode(
            token,
            key,
            algorithms=[ALGORITHM],  # pinned: "none" and RS/HS confusion are impossible
            audience=audience,
            issuer=ISSUER,
            leeway=LEEWAY,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "sid", "typ", "jti"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpired("expired") from exc
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if claims.get("typ") != typ:
        raise TokenError("wrong token type")
    return claims


def new_secret() -> str:
    """256 bits of randomness, URL-safe (refresh tokens, activation and reset links)."""
    return secrets.token_urlsafe(32)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def secrets_equal(a: str, b: str) -> bool:
    """Constant-time comparison, so response timing can't reveal how much matched."""
    return secrets.compare_digest(a.encode(), b.encode())
