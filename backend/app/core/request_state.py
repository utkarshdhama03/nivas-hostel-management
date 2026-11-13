"""Per-request facts that many layers need (logs, audit rows, error bodies).

contextvars are like thread-locals that also work with async code: each request
sees its own values. The middleware sets them; nobody passes them around by hand.
Only an allowlist of request metadata is kept (docs/05 C9): request ID, client IP,
user agent. Never headers like Authorization or Cookie.
"""

import ipaddress
from contextvars import ContextVar

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
client_ip: ContextVar[str | None] = ContextVar("client_ip", default=None)
user_agent: ContextVar[str | None] = ContextVar("user_agent", default=None)


def valid_client_ip() -> str | None:
    """The client IP if it is a real IP address (the database column is `inet`), else None.
    (The test client, for example, reports "testclient".)"""
    value = client_ip.get()
    try:
        return str(ipaddress.ip_address(value)) if value else None
    except ValueError:
        return None
