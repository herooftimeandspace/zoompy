"""Focused retry-behavior tests for the production `ZoomClient`.

The large contract suites prove that the client can speak to schema-defined
Zoom APIs. They are intentionally not the best place to verify low-level retry
policy details such as `Retry-After` handling or transport-exception retries.

These tests isolate the retry loop with a tiny no-op schema registry so the
assertions stay about retry behavior, not about OpenAPI matching.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest

from zoompy import ZoomClient


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
        return None


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
