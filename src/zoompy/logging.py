"""Structured logging helpers for the `zoompy` package.

The library deliberately does not force logging configuration on applications.
Instead, we provide a JSON formatter and a convenience configuration function
that users can opt into when they want observability.

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
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        compact_payload = {
            key: value for key, value in payload.items() if value is not None
        }
        return json.dumps(compact_payload, ensure_ascii=True, sort_keys=True)


def get_logger() -> logging.Logger:
    """Return the package logger and ensure it is safe by default.

    A `NullHandler` prevents the "No handlers could be found" warning while also
    honoring the requirement that the library must not configure logging unless
    the user explicitly opts in.
    """

    logger = logging.getLogger("zoompy")
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    return logger


def configure_logging(level: str = "INFO") -> logging.Logger:
    """Configure the `zoompy` logger to emit JSON logs to stderr.

    This helper is intentionally idempotent. If a JSON stream handler is
    already present, it will not add duplicates on repeated calls.
    """

    logger = logging.getLogger("zoompy")
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
