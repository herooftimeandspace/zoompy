"""Server-to-Server OAuth support for `zoompy`.

This module owns token acquisition and caching so the main client can focus on
request execution instead of credential bookkeeping.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, ConfigDict

from .logging import get_logger


class TokenResponse(BaseModel):
    """Validated representation of Zoom's OAuth token response payload."""

    model_config = ConfigDict(extra="ignore")

    access_token: str
    token_type: str
    expires_in: int


class OAuthTokenManager:
    """Acquire, cache, and refresh Zoom OAuth access tokens.

    The token manager exists so the client can ask for "a valid access token"
    without caring whether the token is cached, expired, or still needs to be
    fetched from Zoom.
    """

    def __init__(
        self,
        *,
        http_client: httpx.Client,
        oauth_url: str,
        account_id: str | None,
        client_id: str | None,
        client_secret: str | None,
        token_skew_seconds: int = 60,
        access_token: str | None = None,
    ) -> None:
        """Initialize the token manager.

        Parameters are stored directly because this object is long-lived and
        intentionally simple. The `access_token` override is especially useful
        in tests, where we want to bypass the live OAuth flow entirely.
        """

        self._http = http_client
        self._oauth_url = oauth_url.rstrip("/")
        self._account_id = account_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_skew_seconds = token_skew_seconds
        self._access_token_override = access_token
        self._cached_token: str | None = None
        self._expires_at_epoch: float = 0.0
        self._lock = threading.Lock()
        self._logger = get_logger()

    def get_access_token(self, *, timeout: float | None = None) -> str:
        """Return a valid access token, refreshing it only when necessary."""

        if self._access_token_override is not None:
            return self._access_token_override

        if self._has_valid_cached_token():
            return self._cached_token or ""

        with self._lock:
            # Double-check inside the lock so concurrent callers do not all
            # refresh the token at the same time.
            if self._has_valid_cached_token():
                return self._cached_token or ""

            token_response = self._fetch_token(timeout=timeout)
            self._cached_token = token_response.access_token
            self._expires_at_epoch = (
                time.time() + token_response.expires_in - self._token_skew_seconds
            )

            expires_at_iso = datetime.fromtimestamp(
                self._expires_at_epoch,
                tz=UTC,
            ).isoformat()
            self._logger.info(
                "Acquired Zoom access token.",
                extra={
                    "event": "token_acquired",
                    "error_type": None,
                    "error_message": None,
                    "path": "/oauth/token",
                    "url": f"{self._oauth_url}/oauth/token",
                    "status_code": 200,
                    "request_id": None,
                    "trace_id": None,
                    "duration_ms": None,
                    "retry_attempt": None,
                    "method": "POST",
                    "token_expires_at": expires_at_iso,
                },
            )
            return self._cached_token

    def _has_valid_cached_token(self) -> bool:
        """Return true when a non-expired cached token is available."""

        return (
            self._cached_token is not None and
            time.time() < self._expires_at_epoch
        )

    def _fetch_token(self, *, timeout: float | None = None) -> TokenResponse:
        """Request a fresh access token from Zoom's OAuth endpoint.

        This method deliberately logs failure details without ever logging raw
        credentials or the token itself.
        """

        if not self._account_id or not self._client_id or not self._client_secret:
            raise ValueError(
                "Zoom OAuth credentials are missing. Set ZOOM_ACCOUNT_ID, "
                "ZOOM_CLIENT_ID, and ZOOM_CLIENT_SECRET or provide an "
                "explicit access_token."
            )

        url = f"{self._oauth_url}/oauth/token"
        try:
            response = self._http.post(
                url,
                params={
                    "grant_type": "account_credentials",
                    "account_id": self._account_id,
                },
                auth=httpx.BasicAuth(self._client_id, self._client_secret),
                timeout=timeout,
            )
            response.raise_for_status()
            return TokenResponse.model_validate(response.json())
        except Exception as exc:
            self._logger.error(
                "Failed to acquire Zoom access token.",
                extra={
                    "event": "token_acquisition_failed",
                    "method": "POST",
                    "url": url,
                    "path": "/oauth/token",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
            raise
