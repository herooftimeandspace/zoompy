"""Focused tests for OAuth token acquisition and caching.

The broader contract suites prove the client can authenticate through its
public request flow, but they do not intentionally walk through all of the
internal token-manager branches. These tests stay close to
`OAuthTokenManager` so future maintainers can change the auth layer with
confidence and still keep the behavior deterministic.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from zoom_sdk.auth import OAuthTokenManager


def _build_manager(
    *,
    account_id: str | None = "acct-123",
    client_id: str | None = "client-123",
    client_secret: str | None = "secret-123",
    access_token: str | None = None,
) -> OAuthTokenManager:
    """Create a token manager with a disposable in-memory HTTP client."""

    return OAuthTokenManager(
        http_client=httpx.Client(),
        oauth_url="https://zoom.example",
        account_id=account_id,
        client_id=client_id,
        client_secret=client_secret,
        access_token=access_token,
    )


def test_access_token_override_short_circuits_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return the explicit token override without consulting OAuth state."""

    manager = _build_manager(access_token="override-token")

    def fail_fetch(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("OAuth fetch should not run when access_token is set.")

    monkeypatch.setattr(manager, "_fetch_token", fail_fetch)
    try:
        assert manager.get_access_token() == "override-token"
    finally:
        manager._http.close()


def test_cached_token_is_reused_without_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reuse a non-expired cached token instead of fetching again."""

    manager = _build_manager()
    manager._cached_token = "cached-token"
    manager._expires_at_epoch = 10_000.0
    monkeypatch.setattr("zoom_sdk.auth.time.time", lambda: 1_000.0)

    def fail_fetch(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Cached token path should not fetch a new token.")

    monkeypatch.setattr(manager, "_fetch_token", fail_fetch)
    try:
        assert manager.get_access_token() == "cached-token"
    finally:
        manager._http.close()


def test_second_cache_check_inside_lock_prevents_duplicate_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Double-check the cache under the lock before fetching a new token."""

    manager = _build_manager()
    checks = 0

    def fake_has_valid_cached_token() -> bool:
        nonlocal checks
        checks += 1
        if checks == 1:
            manager._cached_token = "filled-by-another-caller"
            manager._expires_at_epoch = 10_000.0
            return False
        return True

    def fail_fetch(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Second cache check should prevent token fetch.")

    monkeypatch.setattr(manager, "_has_valid_cached_token", fake_has_valid_cached_token)
    monkeypatch.setattr(manager, "_fetch_token", fail_fetch)
    try:
        assert manager.get_access_token() == "filled-by-another-caller"
        assert checks == 2
    finally:
        manager._http.close()


def test_fetch_token_requires_complete_oauth_credentials() -> None:
    """Raise a clear error when required OAuth settings are missing."""

    manager = _build_manager(account_id=None)
    try:
        with pytest.raises(ValueError, match="ZOOM_ACCOUNT_ID"):
            manager._fetch_token()
    finally:
        manager._http.close()


def test_successful_fetch_caches_token_and_logs_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fetch a token, cache it, and emit a structured success log."""

    manager = _build_manager()
    events: list[dict[str, Any]] = []
    request = httpx.Request("POST", "https://zoom.example/oauth/token")
    response = httpx.Response(
        200,
        json={
            "access_token": "fresh-token",
            "token_type": "bearer",
            "expires_in": 3600,
        },
        request=request,
    )

    def fake_post(*args: Any, **kwargs: Any) -> httpx.Response:
        return response

    monkeypatch.setattr(manager._http, "post", fake_post)
    monkeypatch.setattr("zoom_sdk.auth.time.time", lambda: 1_000.0)
    monkeypatch.setattr(
        manager._logger,
        "info",
        lambda message, *, extra: events.append({"message": message, **extra}),
    )

    try:
        token = manager.get_access_token(timeout=12.0)
    finally:
        manager._http.close()

    assert token == "fresh-token"
    assert manager._cached_token == "fresh-token"
    assert manager._expires_at_epoch == 4_540.0
    assert events == [
        {
            "message": "Acquired Zoom access token.",
            "event": "token_acquired",
            "error_type": None,
            "error_message": None,
            "path": "/oauth/token",
            "url": "https://zoom.example/oauth/token",
            "status_code": 200,
            "request_id": None,
            "trace_id": None,
            "duration_ms": None,
            "retry_attempt": None,
            "method": "POST",
            "token_expires_at": "1970-01-01T01:15:40+00:00",
        }
    ]


def test_fetch_failure_is_logged_before_reraising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Log token-acquisition failures without swallowing the exception."""

    manager = _build_manager()
    events: list[dict[str, Any]] = []
    request = httpx.Request("POST", "https://zoom.example/oauth/token")
    response = httpx.Response(401, json={"message": "nope"}, request=request)

    def fake_post(*args: Any, **kwargs: Any) -> httpx.Response:
        return response

    monkeypatch.setattr(manager._http, "post", fake_post)
    monkeypatch.setattr(
        manager._logger,
        "error",
        lambda message, *, extra: events.append({"message": message, **extra}),
    )

    try:
        with pytest.raises(httpx.HTTPStatusError):
            manager._fetch_token()
    finally:
        manager._http.close()

    assert events[0]["event"] == "token_acquisition_failed"
    assert events[0]["error_type"] == "HTTPStatusError"


def test_invalid_token_payload_is_logged_and_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject malformed token payloads before caching them."""

    manager = _build_manager()
    events: list[dict[str, Any]] = []
    request = httpx.Request("POST", "https://zoom.example/oauth/token")
    response = httpx.Response(
        200,
        json={"token_type": "bearer", "expires_in": 3600},
        request=request,
    )

    def fake_post(*args: Any, **kwargs: Any) -> httpx.Response:
        return response

    monkeypatch.setattr(manager._http, "post", fake_post)
    monkeypatch.setattr(
        manager._logger,
        "error",
        lambda message, *, extra: events.append({"message": message, **extra}),
    )

    try:
        with pytest.raises(ValidationError):
            manager._fetch_token()
    finally:
        manager._http.close()

    assert events[0]["event"] == "token_acquisition_failed"
    assert events[0]["error_type"] == "ValidationError"
