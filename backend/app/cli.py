"""Server-side administration commands, run in the tools container (Q83):

    docker compose run --rm tools python -m app.cli create-platform-owner --email you@example.com --name "Your Name"
    docker compose run --rm tools python -m app.cli reset-platform-owner-password --email you@example.com
    docker compose exec worker python -m app.cli send-test-email --to you@gmail.com

The first two connect as hms_platform (no tenant data). Passwords are typed at a hidden prompt,
never passed on the command line (that would put them in the shell history).

reset-platform-owner-password is the answer to "the Platform Owner forgot the password": only
someone with access to the server can run it (there is no emailed reset for the platform). It sets
the new password, clears the lock and ends every session. (Two-step verification was removed, D87.)

send-test-email sends one sample password-reset email (a random code that unlocks nothing) through
the configured sender: Gmail when hms-mail.env is filled in, Mailpit otherwise (D88). Run it in the
worker container: that is the one that has the mail settings.
"""

from __future__ import annotations

import argparse
import getpass
import sys
import uuid

from sqlalchemy import func, select, update

from app.core import audit, email, keyrotation, otp, passwords
from app.core.config import get_settings
from app.core.db import transaction
from app.core.logging import configure_logging
from app.modules.platform.models import PlatformSession, PlatformUser


class CliError(Exception):
    pass


def create_platform_owner(email: str, full_name: str, password: str) -> uuid.UUID:
    problems = passwords.check_new_password(password, context=(*full_name.split(), email.split("@")[0], "platform"))
    if problems:
        raise CliError("; ".join(p.message for p in problems))
    with transaction(None, role="hms_platform") as db:
        if db.scalars(select(PlatformUser).where(func.lower(PlatformUser.email) == email.lower())).first():
            raise CliError("a platform user with this email already exists")
        user = PlatformUser(email=email, full_name=full_name, password_hash=passwords.hash_password(password))
        db.add(user)
        db.flush()
        audit.record(db, audit.PlatformActor(user.id), "platform_user.created", "platform_user", user.id)
        return user.id


def reset_platform_owner_password(email: str, password: str) -> None:
    with transaction(None, role="hms_platform") as db:
        user = db.scalars(select(PlatformUser).where(func.lower(PlatformUser.email) == email.lower())).first()
        if user is None:
            raise CliError("no platform user with this email")
        problems = passwords.check_new_password(
            password, context=(*user.full_name.split(), email.split("@")[0], "platform")
        )
        if problems:
            raise CliError("; ".join(p.message for p in problems))
        db.execute(
            update(PlatformUser)
            .where(PlatformUser.id == user.id)
            .values(
                password_hash=passwords.hash_password(password),
                failed_login_count=0,
                locked_until=None,
                updated_at=func.now(),
            )
        )
        db.execute(
            update(PlatformSession)
            .where(PlatformSession.platform_user_id == user.id, PlatformSession.revoked_at.is_(None))
            .values(revoked_at=func.now(), revoke_reason="password_changed")
        )
        # Recorded against the account itself: the database only lets hms_platform write
        # platform_user audit rows; the action name says it came from the server console.
        audit.record(
            db, audit.PlatformActor(user.id), "platform_user.password_reset_from_console", "platform_user", user.id
        )


def send_test_email(to: str) -> str:
    """One sample reset-code email through the configured sender. Returns which sender was used."""
    message = email.render(
        "password_reset_code",
        "Your password reset code (test)",
        name="there",
        org_name="Hostel Management",
        code=otp.new_code(),  # random and stored nowhere: it unlocks nothing
        minutes=10,
    )
    email.get_sender().send(email.OutgoingEmail(to=to, subject=message.subject, text=message.text, html=message.html))
    if email.is_test_address(to) or not email.gmail_configured():
        return "Mailpit (http://localhost:8025)"
    return "Gmail (smtp.gmail.com, STARTTLS)"


def _ask_password() -> str:
    first = getpass.getpass("New password: ")
    if first != getpass.getpass("Repeat password: "):
        raise CliError("the passwords don't match")
    return first


def main(argv: list[str] | None = None) -> int:
    configure_logging("WARNING")
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-platform-owner", help="create a Platform Owner account")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    reset = sub.add_parser("reset-platform-owner-password", help="set a new password for a Platform Owner")
    reset.add_argument("--email", required=True)
    test = sub.add_parser("send-test-email", help="send one sample reset-code email (D88)")
    test.add_argument("--to", required=True)
    # Data-key rotation (docs/runbooks/keys.md). Key material is never printed, only key IDs.
    add = sub.add_parser("add-data-key", help="add a new data key to the keyring file and make it active")
    add.add_argument("--keyring", required=True, help="path to data-keys.json (mounted read-write)")
    sub.add_parser("reencrypt-data", help="re-encrypt every item still under an old data key")
    retire = sub.add_parser("retire-data-key", help="remove an old data key once nothing uses it")
    retire.add_argument("--keyring", required=True)
    retire.add_argument("--kid", required=True)
    sub.add_parser("data-key-status", help="count encrypted items per key ID; flag IDs missing from the keyring")
    args = parser.parse_args(argv)
    try:
        if args.command == "create-platform-owner":
            create_platform_owner(args.email, args.name, _ask_password())
            print("Platform Owner created. Sign in at /platform/login with the email and the password.")
        elif args.command == "reset-platform-owner-password":
            reset_platform_owner_password(args.email, _ask_password())
            print("Password set, lock cleared, every session ended.")
        elif args.command == "send-test-email":
            used = send_test_email(args.to)
            print(f"Sent through {used}. Check the inbox (and the spam folder).")
        elif args.command == "add-data-key":
            kid = keyrotation.add_key(args.keyring)
            print(f"New active data key: {kid}. Restart api and worker, then run reencrypt-data.")
        elif args.command == "reencrypt-data":
            r = keyrotation.reencrypt_all()
            print(f"Re-encrypted {r.files} file(s).")
            if r.missing_files:
                ids = ", ".join(str(i) for i in r.missing_files)
                print(f"warning: {len(r.missing_files)} file(s) have no bytes in storage and were skipped: {ids}")
                print("The old key can't be retired while they exist; see docs/runbooks/keys.md.", file=sys.stderr)
            if r.unreadable:
                ids = ", ".join(str(i) for i in r.unreadable)
                print(f"warning: {len(r.unreadable)} item(s) could not be decrypted and were skipped: {ids}")
                print("The old key can't be retired while they exist; see docs/runbooks/keys.md.", file=sys.stderr)
        elif args.command == "data-key-status":
            ring = get_settings().data_keyring()
            usage = keyrotation.key_usage()
            missing = sorted(k for k in usage if k not in ring.keys)
            for kid in sorted(set(ring.keys) | set(usage)):
                mark = " (active)" if kid == ring.active else " MISSING FROM KEYRING" if kid in missing else ""
                print(f"{kid}: {usage.get(kid, 0)} item(s){mark}")
            if missing:
                print(
                    "error: some items use keys that aren't in the keyring; see docs/runbooks/keys.md", file=sys.stderr
                )
                return 1
        else:
            keyrotation.retire_key(args.keyring, args.kid)
            print(f"Key {args.kid} removed from the keyring. Update your offline copy.")
    except (CliError, keyrotation.RotationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
