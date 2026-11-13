"""Structured JSON logs with secret redaction (docs/05 AUTH-12).

Two layers of protection:
1. Code never logs request bodies or headers (the middleware logs method, path,
   status and duration only).
2. This formatter still scrubs anything secret-looking that slips into a message
   or an `extra` field: bearer tokens, JWTs, and values of keys such as password,
   token, otp, secret, authorization, cookie.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

from app.core import request_state

REDACTED = "[REDACTED]"
SECRET_KEY = re.compile(r"(pass(word)?|token|secret|otp|code|authorization|cookie|key)", re.IGNORECASE)
_PATTERNS = [
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"),  # Authorization: Bearer ...
    re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]+"),  # anything shaped like a JWT
    re.compile(r"(?i)((?:password|token|secret|otp|authorization|cookie)\s*[=:]\s*)([^\s,;&]+)"),
]
_STANDARD = set(vars(logging.makeLogRecord({})))


def redact_text(text: str) -> str:
    text = _PATTERNS[0].sub("Bearer " + REDACTED, text)
    text = _PATTERNS[1].sub(REDACTED, text)
    return _PATTERNS[2].sub(lambda m: m.group(1) + REDACTED, text)


def redact_value(key: str, value: Any) -> Any:
    if SECRET_KEY.search(key):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact_value(str(k), v) for k, v in value.items()}
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact_text(record.getMessage()),
            "request_id": request_state.request_id.get(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD and not key.startswith("_"):
                entry[key] = redact_value(key, value)
        if record.exc_info:
            entry["exc"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(entry, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # uvicorn's access log prints full URLs (which could contain tokens); ours replaces it.
    logging.getLogger("uvicorn.access").disabled = True
