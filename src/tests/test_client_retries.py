"""Focused retry-behavior tests for the production `ZoomClient`.

The large contract suites prove that the client can speak to schema-defined
Zoom APIs. They are intentionally not the best place to verify low-level retry
policy details such as `Retry-After` handling or transport-exception retries.

These tests isolate the retry loop with a tiny no-op schema registry so the
assertions stay about retry behavior, not about OpenAPI matching.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import Any

import httpx
import pytest

from zoom_sdk import ZoomClient


class _NoOpSchemaRegistry:
    """Minimal schema registry stub used to isolate retry behavior.

    Retry tests should not fail because a mocked URL is missing from the bundled
    Zoom schemas. This stub provides exactly the two methods `ZoomClient`
    expects and deliberately performs no validation.
    """

    def base_url_for_request(
        self,
        *,
        method: str,
        raw_path: str,
        actual_path: str,
        fallback: str,
    ) -> str:
        _ = (method, raw_path, actual_path)
        return fallback

    def validate_response(
        self,
        *,
        method: str,
        raw_path: str,
        actual_path: str,
        status_code: int,
        payload: Any,
    ) -> None:
        _ = (method, raw_path, actual_path, status_code, payload)
        return None


class _RecordingSchemaRegistry(_NoOpSchemaRegistry):
    """Schema stub that records validation calls for response parsing tests."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def validate_response(
        self,
        *,
        method: str,
        raw_path: str,
        actual_path: str,
        status_code: int,
        payload: Any,
    ) -> None:
        self.calls.append(
            {
                "method": method,
                "raw_path": raw_path,
                "actual_path": actual_path,
                "status_code": status_code,
                "payload": payload,
            }
        )


class _RaisingWebhookRegistry:
    """Webhook stub that raises a controlled validation error."""

    def validate_webhook(self, **kwargs: Any) -> None:
        _ = kwargs
        raise ValueError("bad webhook payload")


class _RaisingSchemaRegistry(_NoOpSchemaRegistry):
    """Schema stub that forces response-validation failures."""

    def validate_response(
        self,
        *,
        method: str,
        raw_path: str,
        actual_path: str,
        status_code: int,
        payload: Any,
    ) -> None:
        _ = (method, raw_path, actual_path, status_code, payload)
        raise ValueError("schema mismatch")


def _build_client() -> ZoomClient:
    """Create a client configured for isolated retry tests."""

    return ZoomClient(
        access_token="test-access-token",
        base_url="https://api.zoom.example",
        max_retries=2,
        backoff_base_seconds=0.5,
        backoff_max_seconds=8.0,
        schema_registry=_NoOpSchemaRegistry(),  # type: ignore[arg-type]
    )


def test_request_retries_transport_errors_once_before_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry the configured transport exceptions and eventually return success."""

    client = _build_client()
    sleeps: list[float] = []
    calls = 0

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    def fake_request(
        method: str,
        url: str,
        *,
        params: Any = None,
        json: Any = None,
        headers: Any = None,
        timeout: Any = None,
    ) -> httpx.Response:
        _ = (params, json, headers, timeout)
        nonlocal calls
        calls += 1
        request = httpx.Request(method, url)
        if calls == 1:
            raise httpx.ConnectError("temporary connect failure", request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    monkeypatch.setattr(time, "sleep", fake_sleep)
    monkeypatch.setattr(client._http, "request", fake_request)

    try:
        result = client.request("GET", "/retry-test")
    finally:
        client.close()

    assert result == {"ok": True}
    assert calls == 2
    assert len(sleeps) == 1


def test_request_uses_retry_after_header_for_rate_limits(
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: Any,
) -> None:
    """Prefer `Retry-After` over exponential backoff for HTTP 429 responses."""

    client = _build_client()
    sleeps: list[float] = []
    url = "https://api.zoom.example/rate-limited"
    attempts = 0

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    def responder(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "3"},
                request=request,
            )
        return httpx.Response(200, json={"ok": True}, request=request)

    monkeypatch.setattr(time, "sleep", fake_sleep)
    respx_mock.get(url).mock(side_effect=responder)

    try:
        result = client.request("GET", "/rate-limited")
    finally:
        client.close()

    assert result == {"ok": True}
    assert attempts == 2
    assert sleeps == [3.0]


def test_request_does_not_retry_non_retriable_401(
    respx_mock: Any,
) -> None:
    """Do not retry ordinary authentication failures like HTTP 401."""

    client = _build_client()
    url = "https://api.zoom.example/unauthorized"
    attempts = 0

    def responder(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(401, json={"message": "nope"}, request=request)

    respx_mock.get(url).mock(side_effect=responder)

    try:
        with pytest.raises(httpx.HTTPStatusError):
            client.request("GET", "/unauthorized")
    finally:
        client.close()

    assert attempts == 1


def test_request_raises_after_retries_are_exhausted(
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: Any,
) -> None:
    """Raise the last HTTP error after retrying a retriable status code."""

    client = _build_client()
    sleeps: list[float] = []
    url = "https://api.zoom.example/unavailable"
    attempts = 0

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    def responder(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, json={"message": "try later"}, request=request)

    monkeypatch.setattr(time, "sleep", fake_sleep)
    respx_mock.get(url).mock(side_effect=responder)

    try:
        with pytest.raises(httpx.HTTPStatusError):
            client.request("GET", "/unavailable")
    finally:
        client.close()

    assert attempts == 3
    assert len(sleeps) == 2


def test_request_logs_final_transport_failure_after_retries_exhaust(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Log the last transport exception when retry attempts run out."""

    client = _build_client()
    errors: list[dict[str, Any]] = []
    request = httpx.Request("GET", "https://api.zoom.example/transport-failure")
    sleeps: list[float] = []
    attempts = 0

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    def fake_request(
        method: str,
        url: str,
        *,
        params: Any = None,
        json: Any = None,
        headers: Any = None,
        timeout: Any = None,
    ) -> httpx.Response:
        _ = (params, json, headers, timeout)
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("still broken", request=request)

    monkeypatch.setattr(time, "sleep", fake_sleep)
    monkeypatch.setattr(client._http, "request", fake_request)
    monkeypatch.setattr(
        client._logger,
        "error",
        lambda message, *, extra: errors.append({"message": message, **extra}),
    )

    try:
        with pytest.raises(httpx.ConnectError):
            client.request("GET", "/transport-failure")
    finally:
        client.close()

    assert attempts == 3
    assert len(sleeps) == 2
    assert errors[0]["event"] == "request_failed"
    assert errors[0]["error_type"] == "ConnectError"


def test_client_context_manager_and_token_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover context-manager helpers and token-manager delegation."""

    client = _build_client()
    close_calls: list[str] = []
    token_calls: list[float | None] = []
    monkeypatch.setattr(client, "close", lambda: close_calls.append("closed"))

    def fake_get_access_token(*, timeout: float | None = None) -> str:
        token_calls.append(timeout)
        return "delegated-token"

    monkeypatch.setattr(
        client._token_manager,
        "get_access_token",
        fake_get_access_token,
    )

    with client as managed:
        assert managed is client

    assert close_calls == ["closed"]
    assert client.get_access_token(timeout=2.5) == "delegated-token"
    assert token_calls == [2.5]


def test_getattr_exposes_sdk_members_and_rejects_unknown_names() -> None:
    """Expose SDK namespaces through `__getattr__` and reject missing ones."""

    client = ZoomClient(access_token="test-access-token")

    try:
        assert client.users is client.sdk.get_member("users")
        with pytest.raises(AttributeError, match="no attribute 'definitely_missing'"):
            _ = client.definitely_missing
    finally:
        client.close()


def test_retry_after_parses_integer_and_http_date_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accept both legal `Retry-After` wire formats."""

    client = _build_client()
    future = datetime(2026, 3, 11, 19, 0, 5, tzinfo=UTC)
    monkeypatch.setattr(time, "time", lambda: future.timestamp() - 5.0)

    try:
        assert client._parse_retry_after("7") == 7.0
        assert client._parse_retry_after(format_datetime(future)) == 5.0
    finally:
        client.close()


def test_retry_after_rejects_malformed_or_timezone_free_values() -> None:
    """Fall back cleanly when `Retry-After` cannot be trusted."""

    client = _build_client()

    try:
        assert client._parse_retry_after(None) is None
        assert client._parse_retry_after("   ") is None
        assert client._parse_retry_after("not-a-date") is None
        assert client._parse_retry_after("Wed, 11 Mar 2026 19:00:05") is None
    finally:
        client.close()


def test_retry_delay_uses_backoff_when_retry_after_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed 429 headers should not break retry scheduling."""

    client = _build_client()
    request = httpx.Request("GET", "https://api.zoom.example/rate-limited")
    response = httpx.Response(
        429,
        headers={"Retry-After": "later maybe"},
        request=request,
    )

    monkeypatch.setattr(client, "_calculate_backoff", lambda *, attempt: 1.25)

    try:
        assert client._retry_delay_from_response(response=response, attempt=1) == 1.25
    finally:
        client.close()


def test_request_runtime_fallback_raises_when_retry_loop_never_runs() -> None:
    """Keep the explicit impossible-state guard covered for type-checker clarity."""

    client = ZoomClient(
        access_token="test-access-token",
        base_url="https://api.zoom.example",
        max_retries=-1,
        schema_registry=_NoOpSchemaRegistry(),  # type: ignore[arg-type]
    )

    try:
        with pytest.raises(
            RuntimeError,
            match="Request loop completed without a response or exception",
        ):
            client.request("GET", "/never-runs")
    finally:
        client.close()


def test_request_returns_none_for_204_and_validates_empty_payload(
    respx_mock: Any,
) -> None:
    """Treat `204 No Content` as a successful `None` response."""

    registry = _RecordingSchemaRegistry()
    client = ZoomClient(
        access_token="test-access-token",
        base_url="https://api.zoom.example",
        schema_registry=registry,  # type: ignore[arg-type]
    )
    url = "https://api.zoom.example/no-content"
    respx_mock.get(url).mock(
        return_value=httpx.Response(
            204,
            request=httpx.Request("GET", url),
        )
    )

    try:
        result = client.request("GET", "/no-content")
    finally:
        client.close()

    assert result is None
    assert registry.calls == [
        {
            "method": "GET",
            "raw_path": "/no-content",
            "actual_path": "/no-content",
            "status_code": 204,
            "payload": None,
        }
    ]


def test_request_rejects_invalid_json_response_bodies(
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: Any,
) -> None:
    """Raise `ValueError` when a successful response body is not valid JSON."""

    client = _build_client()
    errors: list[dict[str, Any]] = []
    url = "https://api.zoom.example/not-json"
    request = httpx.Request("GET", url)
    respx_mock.get(url).mock(
        return_value=httpx.Response(
            200,
            content=b"definitely not json",
            headers={"Content-Type": "application/json"},
            request=request,
        )
    )
    monkeypatch.setattr(
        client._logger,
        "error",
        lambda message, *, extra: errors.append({"message": message, **extra}),
    )

    try:
        with pytest.raises(ValueError, match="Expected JSON response"):
            client.request("GET", "/not-json")
    finally:
        client.close()

    assert errors[0]["event"] == "invalid_json_response"


def test_request_logs_schema_validation_failures(
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: Any,
) -> None:
    """Log and re-raise response schema mismatches from the registry."""

    client = ZoomClient(
        access_token="test-access-token",
        base_url="https://api.zoom.example",
        schema_registry=_RaisingSchemaRegistry(),  # type: ignore[arg-type]
    )
    errors: list[dict[str, Any]] = []
    url = "https://api.zoom.example/schema-error"
    request = httpx.Request("GET", url)
    respx_mock.get(url).mock(
        return_value=httpx.Response(
            200,
            json={"ok": True},
            request=request,
        )
    )
    monkeypatch.setattr(
        client._logger,
        "error",
        lambda message, *, extra: errors.append({"message": message, **extra}),
    )

    try:
        with pytest.raises(ValueError, match="schema mismatch"):
            client.request("GET", "/schema-error")
    finally:
        client.close()

    assert errors[0]["event"] == "schema_validation_failed"
    assert errors[0]["status_code"] == 200


def test_build_headers_includes_authorization_accept_and_custom_headers() -> None:
    """Merge caller headers into the default auth and JSON-accept headers."""

    client = _build_client()

    try:
        headers = client._build_headers(
            {"X-Test": "value", "Accept": "application/scim+json"},
            timeout=1.0,
        )
    finally:
        client.close()

    assert headers["authorization"] == "Bearer test-access-token"
    assert headers["accept"] == "application/scim+json"
    assert headers["x-test"] == "value"


def test_render_path_quotes_values_and_rejects_missing_params() -> None:
    """Quote unsafe path values and reject unresolved placeholders."""

    client = _build_client()

    try:
        assert (
            client._render_path("/users/{userId}", {"userId": "me/name"})
            == "/users/me%2Fname"
        )
        with pytest.raises(ValueError, match="Unresolved path parameters"):
            client._render_path("/users/{userId}", None)
    finally:
        client.close()


def test_dir_falls_back_to_normal_attributes_if_sdk_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep `dir(client)` safe even if SDK construction is temporarily broken."""

    client = _build_client()
    original_sdk = ZoomClient.sdk
    monkeypatch.setattr(
        ZoomClient,
        "sdk",
        property(lambda self: (_ for _ in ()).throw(RuntimeError("boom"))),
    )

    try:
        names = client.__dir__()
    finally:
        monkeypatch.setattr(ZoomClient, "sdk", original_sdk)
        client.close()

    assert "request" in names


def test_validate_webhook_logs_runtime_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Surface webhook validation failures with a structured log entry."""

    client = ZoomClient(
        access_token="test-access-token",
        webhook_registry=_RaisingWebhookRegistry(),  # type: ignore[arg-type]
    )
    errors: list[dict[str, Any]] = []
    monkeypatch.setattr(
        client._logger,
        "error",
        lambda message, *, extra: errors.append({"message": message, **extra}),
    )

    try:
        with pytest.raises(ValueError, match="bad webhook payload"):
            client.validate_webhook("meeting.started", {"event": "meeting.started"})
    finally:
        client.close()

    assert errors[0]["event"] == "webhook_schema_validation_failed"
    assert errors[0]["path"] == "meeting.started"
