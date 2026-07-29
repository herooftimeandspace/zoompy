"""Focused tests for structured package logging helpers.

The logging module is intentionally small, but it is important enough to merit
direct tests because callers rely on these functions for observability and for
safe default behavior around handlers.
"""

from __future__ import annotations

import json
import logging
import sys

from zoom_sdk.logging import JsonLogFormatter, configure_logging, get_logger


def _reset_zoom_logger() -> logging.Logger:
    """Return the package logger in a known-clean state for each test."""

    logger = logging.getLogger("zoom_sdk")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    logger.propagate = True
    logger.setLevel(logging.NOTSET)
    return logger


def test_json_formatter_emits_expected_fields_and_omits_none() -> None:
    """Render a compact JSON object with only populated structured fields."""

    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="zoom_sdk",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    record.event = "request_attempt"
    record.method = "GET"
    record.path = "/users"
    record.token_expires_at = "1970-01-01T00:00:00+00:00"

    payload = json.loads(formatter.format(record))

    assert payload["logger"] == "zoom_sdk"
    assert payload["message"] == "hello world"
    assert payload["event"] == "request_attempt"
    assert payload["method"] == "GET"
    assert payload["path"] == "/users"
    assert payload["token_expires_at"] == "1970-01-01T00:00:00+00:00"
    assert "status_code" not in payload
    assert "timestamp" in payload


def test_json_formatter_includes_serialized_exception() -> None:
    """Attach an exception string when `exc_info` is present on the record."""

    formatter = JsonLogFormatter()
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record = logging.LogRecord(
            name="zoom_sdk",
            level=logging.ERROR,
            pathname=__file__,
            lineno=42,
            msg="failed",
            args=(),
            exc_info=sys.exc_info(),
        )

    payload = json.loads(formatter.format(record))

    assert payload["message"] == "failed"
    assert "exception" in payload
    assert "RuntimeError: boom" in payload["exception"]


def test_get_logger_sets_info_and_adds_null_handler_once() -> None:
    """Default logger setup should be safe and idempotent."""

    logger = _reset_zoom_logger()

    first = get_logger()
    second = get_logger()

    assert first is second is logger
    assert logger.level == logging.INFO
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], logging.NullHandler)


def test_configure_logging_adds_json_stream_handler() -> None:
    """Enable JSON stderr logging when the caller opts in explicitly."""

    logger = _reset_zoom_logger()
    configured = configure_logging("DEBUG")

    assert configured is logger
    assert logger.level == logging.DEBUG
    assert logger.propagate is False
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], logging.StreamHandler)
    assert isinstance(logger.handlers[0].formatter, JsonLogFormatter)
    assert logger.handlers[0].level == logging.DEBUG


def test_configure_logging_reuses_existing_json_handler() -> None:
    """Do not stack duplicate JSON handlers on repeated configuration calls."""

    logger = _reset_zoom_logger()
    configure_logging("INFO")
    handler = logger.handlers[0]

    configure_logging("WARNING")

    assert len(logger.handlers) == 1
    assert logger.handlers[0] is handler
    assert logger.handlers[0].level == logging.WARNING
    assert logger.level == logging.WARNING
