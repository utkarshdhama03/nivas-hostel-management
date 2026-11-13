"""Sending email (docs/02 §10, B7).

    EmailSender  the interface: send(OutgoingEmail)
    SmtpSender   plain SMTP to Mailpit (Q2): catches everything, nothing leaves the machine;
                 or, with credentials, STARTTLS + login to Gmail (D88)
    RoutingSender  Gmail for real addresses, Mailpit for test ones (below)

D88: when HMS_GMAIL_ADDRESS and HMS_GMAIL_APP_PASSWORD are both set (in the secrets folder's
hms-mail.env), email goes out through smtp.gmail.com:587 with STARTTLS, signed in with the app
password, sent from that address. Without them everything goes to Mailpit, as before.
Even with Gmail on, an address at a reserved test domain (RFC 2606/6761: .example, example.com,
.test, .invalid, .localhost, and our own hms.local) still goes to Mailpit: the demo data and the
browser tests use such addresses, and they must never reach the internet or use up Gmail's
daily sending limit (about 500 messages a day for a personal account).

Email is always sent by the WORKER, never inside a request: a slow or failing mail
server must not slow down or fail the request, and failed sends are retried with
backoff by the job system.

Templates are Jinja2 files in app/templates/email/, each with a plain-text and an
HTML version. HTML templates are auto-escaped, so a user-controlled value (a
complaint title, a name) can't inject markup into an email.
"""

from __future__ import annotations

import smtplib
import ssl
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import make_msgid
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.core.config import get_settings

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates" / "email"


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    text: str
    html: str
    # Stable per job (see message_id_for_job): if a retry sends the same email twice,
    # both copies carry the same Message-ID, so the duplicate is identifiable.
    message_id: str | None = None


def message_id_for_job(job_id: uuid.UUID) -> str:
    return f"<job-{job_id}@hms.local>"


class EmailSender(Protocol):
    def send(self, email: OutgoingEmail) -> None: ...


@lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(enabled_extensions=("html",), default_for_string=False),
        undefined=StrictUndefined,  # a missing variable is an error, not a blank in a sent email
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render(template: str, subject: str, **context: Any) -> OutgoingEmail:
    """Render <template>.txt and <template>.html. `to` is filled in by the caller."""
    env = _env()
    ctx = {"subject": subject, **context}
    return OutgoingEmail(
        to="",
        subject=subject,
        text=env.get_template(f"{template}.txt").render(ctx),
        html=env.get_template(f"{template}.html").render(ctx),
    )


class SmtpSender:
    def __init__(
        self,
        host: str,
        port: int,
        sender: str,
        timeout: float = 10.0,
        *,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self.host, self.port, self.sender, self.timeout = host, port, sender, timeout
        self.username, self.password = username, password

    def send(self, email: OutgoingEmail) -> None:
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = email.to
        msg["Subject"] = email.subject
        msg["Message-ID"] = email.message_id or make_msgid(domain="hms.local")
        msg.set_content(email.text)
        msg.add_alternative(email.html, subtype="html")
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
            if self.username and self.password:
                # A real provider (Gmail, D88): encrypt first, then sign in. The certificate is checked.
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(self.username, self.password)
            # Otherwise Mailpit: plain SMTP on the internal Docker network; nothing leaves the machine.
            smtp.send_message(msg)


TEST_DOMAINS = (".example", ".test", ".invalid", ".localhost", ".local")
TEST_HOSTS = ("example.com", "example.org", "example.net")


def is_test_address(address: str) -> bool:
    """Reserved test domains (RFC 2606, RFC 6761) and hms.local: never real mailboxes."""
    domain = address.rsplit("@", 1)[-1].strip().lower().rstrip(">")
    return (
        domain in TEST_HOSTS
        or domain.endswith(TEST_DOMAINS)
        or domain in {d.lstrip(".") for d in TEST_DOMAINS}
        or any(domain.endswith("." + h) for h in TEST_HOSTS)
    )


class RoutingSender:
    """Gmail for real addresses, Mailpit for test addresses (D88)."""

    def __init__(self, real: EmailSender, test: EmailSender) -> None:
        self.real, self.test = real, test

    def send(self, email: OutgoingEmail) -> None:
        (self.test if is_test_address(email.to) else self.real).send(email)


def mailpit_sender() -> SmtpSender:
    s = get_settings()
    return SmtpSender(s.hms_smtp_host, s.hms_smtp_port, s.hms_mail_from)


def gmail_configured() -> bool:
    s = get_settings()
    return bool(s.hms_gmail_address and s.hms_gmail_app_password and s.hms_gmail_app_password.get_secret_value())


def gmail_sender() -> SmtpSender:
    s = get_settings()
    assert s.hms_gmail_address is not None and s.hms_gmail_app_password is not None
    return SmtpSender(
        s.hms_gmail_host,
        s.hms_gmail_port,
        f"Hostel Management <{s.hms_gmail_address}>",
        timeout=20.0,
        username=s.hms_gmail_address,
        # Google shows an app password in groups of four ("abcd efgh ijkl mnop"); the spaces aren't part of it.
        password=s.hms_gmail_app_password.get_secret_value().replace(" ", ""),
    )


@lru_cache(maxsize=1)
def get_sender() -> EmailSender:
    if not gmail_configured():
        return mailpit_sender()
    return RoutingSender(real=gmail_sender(), test=mailpit_sender())
