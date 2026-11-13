"""Typed configuration read from environment variables.

Why pydantic-settings: every setting is declared once, with a type, and the app
fails at start-up with a clear message if something required is missing,
instead of failing later in the middle of a request.

Secrets arrive as environment variables from the env files in the secrets folder
outside the project (compose.yaml). Each role's password is optional here because
each container only receives the passwords it needs: the API never gets the owner
or superuser password.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

# Role name -> name of the environment variable holding its password.
_PASSWORD_ENV = {
    "hms_app": "hms_app_password",
    "hms_worker": "hms_worker_password",
    "hms_platform": "hms_platform_password",
    "hms_owner": "hms_owner_password",
    "hms_backup": "hms_backup_password",
    "postgres": "postgres_password",
}


class ConfigError(RuntimeError):
    """Configuration is missing or malformed. Raised at start-up, never during a request."""


@dataclass(frozen=True)
class KeySet:
    """A set of symmetric keys identified by key ID. `active` is used for new work; the
    others remain only so that older data (or tokens) can still be verified/decrypted."""

    active: str
    keys: dict[str, bytes]

    def active_key(self) -> bytes:
        return self.keys[self.active]


def _b64decode(value: str) -> bytes:
    """Accept standard and URL-safe base64, with or without padding."""
    normalized = value.strip().replace("-", "+").replace("_", "/")
    try:
        return base64.b64decode(normalized + "=" * (-len(normalized) % 4), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ConfigError("key material is not valid base64") from exc


def parse_keyset(spec: str, name: str) -> KeySet:
    """Parse '<kid>:<base64>[,<kid>:<base64>...]'. The first entry is the active key."""
    keys: dict[str, bytes] = {}
    order: list[str] = []
    for part in [p.strip() for p in spec.split(",") if p.strip()]:
        kid, sep, material = part.partition(":")
        if not sep or not kid:
            raise ConfigError(f"{name}: each entry must look like '<key-id>:<base64>'")
        key = _b64decode(material)
        if len(key) != 32:
            raise ConfigError(f"{name}: key {kid!r} must be exactly 32 bytes, got {len(key)}")
        keys[kid] = key
        order.append(kid)
    if not order:
        raise ConfigError(f"{name} is empty")
    return KeySet(active=order[0], keys=keys)


def load_data_keyring(path: str) -> KeySet:
    """The AES keyring file (docs/03 §5.5.1): {"active": "<kid>", "keys": {"<kid>": "<base64>"}}."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"data keyring not found at {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError("data keyring is not valid JSON") from exc
    if not isinstance(raw, dict) or "active" not in raw or not isinstance(raw.get("keys"), dict):
        raise ConfigError("data keyring must contain 'active' and 'keys'")
    keys = {}
    for kid, material in raw["keys"].items():
        key = _b64decode(material)
        if len(key) != 32:
            raise ConfigError(f"data key {kid!r} must be exactly 32 bytes, got {len(key)}")
        keys[kid] = key
    if raw["active"] not in keys:
        raise ConfigError("the active data key ID is not in the keyring")
    return KeySet(active=raw["active"], keys=keys)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False, extra="ignore")

    # --- Database ------------------------------------------------------------------------
    hms_db_host: str = "db"
    hms_db_port: int = 5432
    hms_db_name: str = "hms"
    hms_test_db_name: str = "hms_test"

    # --- Keys (all from outside the project) ------------------------------------------------
    hms_data_keys_file: str = "/run/secrets/data_keys"
    hms_jwt_org_keys: SecretStr | None = None
    hms_jwt_platform_keys: SecretStr | None = None

    # --- Email: Mailpit, or Gmail when both Gmail values are set (D88) -------------------------
    hms_smtp_host: str = "mailpit"  # Mailpit: development inbox, and every test address (app/core/email.py)
    hms_smtp_port: int = 1025
    # From ${HMS_SECRETS_DIR}/hms-mail.env (compose loads it for the API and the worker only).
    hms_gmail_address: str | None = None  # the username and the sender
    hms_gmail_app_password: SecretStr | None = None  # a Gmail app password, never the account password
    hms_gmail_host: str = "smtp.gmail.com"
    hms_gmail_port: int = 587  # STARTTLS
    hms_mail_from: str = "Hostel Management <no-reply@hms.local>"
    hms_email_daily_cap: int | None = None  # None = unlimited (Mailpit); set when a real provider is used
    hms_public_url: str = "http://localhost:5173"  # where links in emails point (the web app)
    # Q72: 3 self-registrations per IP per hour. Only scripts/e2e.sh raises it, for its own run.
    hms_registrations_per_ip_hour: int = 3
    hms_upload_dir: str = "/data/uploads"  # uploaded files (02 §9); a Docker volume, never served statically

    # --- Passwords: SecretStr prints as '**********', never the value --------------------------
    hms_app_password: SecretStr | None = None
    hms_worker_password: SecretStr | None = None
    hms_platform_password: SecretStr | None = None
    hms_owner_password: SecretStr | None = None
    hms_backup_password: SecretStr | None = None
    postgres_password: SecretStr | None = None

    def db_url(self, role: str, database: str | None = None) -> URL:
        """SQLAlchemy URL for connecting as `role`. URL.create escapes the password safely."""
        secret: SecretStr | None = getattr(self, _PASSWORD_ENV[role])
        if secret is None:
            raise ConfigError(f"No password configured for database role {role!r} in this container")
        return URL.create(
            "postgresql+psycopg",
            username=role,
            password=secret.get_secret_value(),
            host=self.hms_db_host,
            port=self.hms_db_port,
            database=database or self.hms_db_name,
        )

    def conninfo(self, role: str, database: str | None = None) -> str:
        """libpq connection string for plain psycopg (used by the database proof tests)."""
        url = self.db_url(role, database)
        return url.set(drivername="postgresql").render_as_string(hide_password=False)

    def jwt_keys(self, audience: str) -> KeySet:
        """Signing keys for organization tokens ('hms-api') or platform tokens ('hms-platform')."""
        secret = self.hms_jwt_org_keys if audience == "hms-api" else self.hms_jwt_platform_keys
        name = "HMS_JWT_ORG_KEYS" if audience == "hms-api" else "HMS_JWT_PLATFORM_KEYS"
        if secret is None:
            raise ConfigError(f"{name} is not set")
        return parse_keyset(secret.get_secret_value(), name)

    def data_keyring(self) -> KeySet:
        return load_data_keyring(self.hms_data_keys_file)


@lru_cache
def get_settings() -> Settings:
    return Settings()
