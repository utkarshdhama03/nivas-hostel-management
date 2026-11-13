"""Start-up checks: refuse to run in an unsafe configuration (docs/05 MT-11, OPS-4).

Run once when the API or the worker starts. Any failure stops the process with a
clear message, which is better than serving requests that silently bypass a
safety layer:

* The database role must NOT be a superuser, have BYPASSRLS, or be (a member of)
  hms_owner. Any of those would switch Row-Level Security off for this process,
  removing the second tenant-isolation layer without anyone noticing.
* The data keyring must load, and its active key must actually encrypt and
  decrypt (otherwise opening an identity document would fail later, at the worst moment).
* The JWT signing keys must be present and well-formed.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from app.core import crypto
from app.core.config import ConfigError, get_settings
from app.core.db import AppRole, engine

log = logging.getLogger("hms.startup")


def check_database_role(role: AppRole, database: str | None = None) -> None:
    with engine(role, database).connect() as conn:
        row = conn.execute(
            text(
                "SELECT current_user, r.rolsuper, r.rolbypassrls, pg_has_role(current_user, 'hms_owner', 'MEMBER') "
                "FROM pg_roles r WHERE r.rolname = current_user"
            )
        ).one()
    user, is_super, bypass_rls, owner_member = row
    if user != role:
        raise ConfigError(f"connected as {user!r}, expected {role!r}")
    if is_super or bypass_rls or owner_member:
        raise ConfigError(
            f"database role {role!r} is a superuser, has BYPASSRLS or belongs to hms_owner: "
            "Row-Level Security would not apply. Refusing to start."
        )


def check_keys(audiences: tuple[str, ...] = ("hms-api", "hms-platform")) -> None:
    settings = get_settings()
    crypto.self_test(settings.data_keyring())
    for audience in audiences:
        settings.jwt_keys(audience)


def run_startup_checks(roles: tuple[AppRole, ...], audiences: tuple[str, ...] = ("hms-api", "hms-platform")) -> None:
    for role in roles:
        check_database_role(role)
    check_keys(audiences)
    log.info("startup checks passed", extra={"roles": list(roles)})
