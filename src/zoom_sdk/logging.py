"""Structured logging helpers for the `zoom_sdk` package.

The package logger defaults to the `INFO` level so applications can receive the
normal request lifecycle immediately after attaching their own handlers.
`zoom_sdk` still does not choose an output destination on behalf of the caller:
applications remain responsible for attaching console, file, or other logging
handlers.

Only the standard library `logging` module is used here, per the repository's
dependency constraints.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any


class JsonLogFormatter(logging.Formatter):
    """Format log records as single-line JSON objects.

    The formatter extracts a mix of standard logging fields and optional
    structured context values stored on the log record via `extra=...`.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Render one log record as valid JSON.

        We build the payload explicitly rather than serializing `record.__dict__`
        wholesale. That keeps the output stable, avoids noisy internal logging
        fields, and reduces the risk of accidentally leaking sensitive data.
        """

        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "event": getattr(record, "event", None),
            "request_id": getattr(record, "request_id", None),
            "trace_id": getattr(record, "trace_id", None),
            "method": getattr(record, "method", None),
            "url": getattr(record, "url", None),
            "path": getattr(record, "path", None),
            "status_code": getattr(record, "status_code", None),
            "duration_ms": getattr(record, "duration_ms", None),
            "retry_attempt": getattr(record, "retry_attempt", None),
            "error_type": getattr(record, "error_type", None),
            "error_message": getattr(record, "error_message", None),
            "token_expires_at": getattr(record, "token_expires_at", None),
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        compact_payload = {
            key: value for key, value in payload.items() if value is not None
        }
        return json.dumps(compact_payload, ensure_ascii=True, sort_keys=True)


def get_logger() -> logging.Logger:
    """Return the package logger and ensure it is safe by default.

    The library defaults to the `INFO` level so application code can see the
    normal request lifecycle as soon as it attaches its own handlers. A
    `NullHandler` still prevents "No handlers could be found" warnings and
    keeps the library from deciding on behalf of the application where log
    output should go.
    """

    logger = logging.getLogger("zoom_sdk")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    return logger


def configure_logging(level: str = "INFO") -> logging.Logger:
    """Configure the `zoom_sdk` logger to emit JSON logs to stderr.

    This helper is intentionally idempotent. If a JSON stream handler is
    already present, it will not add duplicates on repeated calls.
    """

    logger = logging.getLogger("zoom_sdk")
    logger.setLevel(level.upper())
    logger.propagate = False

    for handler in logger.handlers:
        if isinstance(handler.formatter, JsonLogFormatter):
            handler.setLevel(level.upper())
            return logger

    handler = logging.StreamHandler()
    handler.setLevel(level.upper())
    handler.setFormatter(JsonLogFormatter())
    logger.handlers = [handler]
    return logger
