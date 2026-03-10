"""Unified Zoom API client for the `zoompy` package.

The low-level runtime core is still the generic `request()` method because that
is where authentication, retries, logging, and schema validation belong.
On top of that core, the client now exposes a schema-driven SDK surface such as
`client.users.get(...)` and `client.phone.users.list(...)`.
"""

from __future__ import annotations

import random
import time
from collections.abc import Mapping
from email.utils import parsedate_to_datetime
from typing import Any, cast
from urllib.parse import quote

import httpx

from .auth import OAuthTokenManager
from .config import ZoomSettings
from .logging import get_logger
from .schema import SchemaRegistry, WebhookRegistry
from .sdk import ZoomSdk

RETRIABLE_STATUS_CODES = {429, 500, 502, 503, 504}
RETRIABLE_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.RemoteProtocolError,
    httpx.PoolTimeout,
)


class ZoomClient:
    """Production-ready unified client for the Zoom REST API.

    The client owns five responsibilities:

    1. reading configuration
    2. acquiring and caching OAuth access tokens
    3. executing HTTP requests with retries and backoff
    4. validating JSON responses against bundled OpenAPI schemas
    5. emitting structured logs when logging is enabled

    Keeping those responsibilities together in one class gives library users a
    small, predictable public API while still keeping the implementation itself
    modular through helper modules.
    """

    def __init__(
        self,
        *,
        account_id: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        base_url: str | None = None,
        oauth_url: str | None = None,
        token_skew_seconds: int | None = None,
        access_token: str | None = None,
        max_retries: int = 3,
        backoff_base_seconds: float = 0.5,
        backoff_max_seconds: float = 8.0,
        timeout: float = 30.0,
        load_dotenv: bool = True,
        http_client: httpx.Client | None = None,
        schema_registry: SchemaRegistry | None = None,
        webhook_registry: WebhookRegistry | None = None,
    ) -> None:
        """Initialize the client and all supporting components.

        Parameters
        ----------
        access_token:
            When provided, OAuth token acquisition is bypassed entirely. This is
            especially useful in tests and in environments where an external
            token broker already exists.
        http_client:
            Optional dependency injection point for advanced users or tests. When
            omitted, the client creates and owns its own `httpx.Client`.
        schema_registry:
            Optional override for the path-based schema registry. Advanced users
            and focused tests can supply a custom registry instance, but the
            default bundled endpoint and master-account documents are usually
            what you want.
        webhook_registry:
            Optional override for the runtime webhook validator. When omitted,
            the client loads the bundled webhook documents automatically.
        """

        settings = ZoomSettings.from_environment(
            load_local_env=load_dotenv,
        ).merged_with(
            account_id=account_id,
            client_id=client_id,
            client_secret=client_secret,
            base_url=base_url,
            oauth_url=oauth_url,
            token_skew_seconds=token_skew_seconds,
        )

        self._base_url = settings.base_url.rstrip("/")
        self._default_timeout = timeout
        self._max_retries = max_retries
        self._backoff_base_seconds = backoff_base_seconds
        self._backoff_max_seconds = backoff_max_seconds
        self._logger = get_logger()
        self._schemas = schema_registry or SchemaRegistry()
        self._webhooks = webhook_registry or WebhookRegistry()
        self._http = http_client or httpx.Client()
        self._owns_http_client = http_client is None
        self._sdk: ZoomSdk | None = None
        self._token_manager = OAuthTokenManager(
            http_client=self._http,
            oauth_url=settings.oauth_url,
            account_id=settings.account_id,
            client_id=settings.client_id,
            client_secret=settings.client_secret,
            token_skew_seconds=settings.token_skew_seconds,
            access_token=access_token,
        )

    def __enter__(self) -> ZoomClient:
        """Support context-manager usage with `with ZoomClient() as client:`."""

        return self

    def __exit__(self, *args: object) -> None:
        """Close owned resources when leaving a context manager block."""

        self.close()

    def __getattr__(self, name: str) -> Any:
        """Expose generated SDK namespaces as top-level client attributes.

        This keeps the original low-level API intact while also enabling
        ergonomic access patterns such as `client.users.list(...)` and
        `client.phone.users.get(...)`.
        """

        sdk = self.sdk
        if sdk.has_member(name):
            return sdk.get_member(name)
        raise AttributeError(f"{type(self).__name__!s} has no attribute {name!r}")

    def __dir__(self) -> list[str]:
        """Include generated SDK namespaces in interactive discovery."""

        names = set(super().__dir__())
        try:
            names.update(dir(self.sdk))
        except Exception:
            # `dir()` should stay safe even if schema loading fails for some
            # reason. Falling back to the normal attribute list keeps
            # introspection usable instead of surprising callers with errors.
            pass
        return sorted(names)

    def close(self) -> None:
        """Close the underlying HTTP client when this instance owns it."""

        if self._owns_http_client:
            self._http.close()

    def get_access_token(self, *, timeout: float | None = None) -> str:
        """Expose token acquisition for integration tests and advanced callers."""

        return self._token_manager.get_access_token(timeout=timeout)

    @property
    def sdk(self) -> ZoomSdk:
        """Return the lazily constructed dynamic SDK root.

        The generic request client remains the runtime core, but many users want
        a friendlier service-object surface for automation scripts. Building the
        SDK lazily avoids work for callers that only ever use `request()`.
        """

        if self._sdk is None:
            self._sdk = ZoomSdk(client=self, schema_registry=self._schemas)
        return self._sdk

    def validate_webhook(
        self,
        event_name: str,
        payload: Any,
        *,
        schema_name: str | None = None,
        operation_id: str | None = None,
    ) -> None:
        """Validate one incoming Zoom webhook payload.

        This is the runtime counterpart to the repository's schema-driven
        webhook tests. Callers can use it inside their webhook endpoint handler
        to confirm that the incoming JSON still matches Zoom's published
        contract for that event.

        Parameters
        ----------
        event_name:
            The webhook event type, for example `meeting.started`.
        payload:
            The parsed JSON body received from Zoom.
        schema_name:
            Optional product-family hint used when the same event name could
            plausibly exist in more than one schema file.
        operation_id:
            Optional exact OpenAPI operation id for callers that want the most
            specific possible lookup.
        """

        try:
            self._webhooks.validate_webhook(
                event_name=event_name,
                payload=payload,
                schema_name=schema_name,
                operation_id=operation_id,
            )
        except ValueError as exc:
            self._logger.error(
                "Webhook schema validation failed.",
                extra={
                    "event": "webhook_schema_validation_failed",
                    "path": event_name,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
            raise

    def request(
        self,
        method: str,
        path: str,
        *,
        path_params: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any] | list[Any] | None:
        """Execute one Zoom API request and validate the response schema.

        The method intentionally returns plain Python data structures (`dict`,
        `list`, or `None`) because that matches the contract-test suite and
        keeps the library ergonomic for callers who simply want validated JSON.

        The same request method transparently supports both ordinary Zoom
        endpoints and master-account endpoints. The runtime schema registry
        loads both document families and chooses the matching OpenAPI operation
        automatically from the request path.
        """

        raw_path = path if path.startswith("/") else f"/{path}"
        actual_path = self._render_path(raw_path, path_params)
        request_timeout = timeout if timeout is not None else self._default_timeout
        normalized_method = method.upper()
        base_url = self._schemas.base_url_for_request(
            method=normalized_method,
            raw_path=raw_path,
            actual_path=actual_path,
            fallback=self._base_url,
        )
        url = self._build_url(actual_path, base_url=base_url)
        request_headers = self._build_headers(headers, timeout=request_timeout)

        last_response: httpx.Response | None = None
        last_exception: Exception | None = None

        for attempt in range(self._max_retries + 1):
            started_at = time.monotonic()
            self._log_request_attempt(
                method=normalized_method,
                url=url,
                path=actual_path,
                retry_attempt=attempt,
            )

            try:
                response = self._http.request(
                    normalized_method,
                    url,
                    params=dict(params) if params is not None else None,
                    json=json,
                    headers=request_headers,
                    timeout=request_timeout,
                )
                last_response = response
            except RETRIABLE_EXCEPTIONS as exc:
                last_exception = exc
                duration_ms = self._duration_ms(started_at)
                should_retry = attempt < self._max_retries
                if should_retry:
                    sleep_seconds = self._calculate_backoff(attempt=attempt)
                    self._log_retry(
                        method=normalized_method,
                        url=url,
                        path=actual_path,
                        retry_attempt=attempt + 1,
                        duration_ms=duration_ms,
                        reason=str(exc),
                        sleep_seconds=sleep_seconds,
                        status_code=None,
                    )
                    time.sleep(sleep_seconds)
                    continue

                self._logger.error(
                    "Zoom request failed after retries were exhausted.",
                    extra={
                        "event": "request_failed",
                        "method": normalized_method,
                        "url": url,
                        "path": actual_path,
                        "duration_ms": duration_ms,
                        "retry_attempt": attempt,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )
                raise

            duration_ms = self._duration_ms(started_at)
            self._log_response(
                method=normalized_method,
                url=url,
                path=actual_path,
                response=response,
                duration_ms=duration_ms,
            )

            if self._should_retry_response(response) and attempt < self._max_retries:
                sleep_seconds = self._retry_delay_from_response(
                    response=response,
                    attempt=attempt,
                )
                self._log_retry(
                    method=normalized_method,
                    url=url,
                    path=actual_path,
                    retry_attempt=attempt + 1,
                    duration_ms=duration_ms,
                    reason=f"HTTP {response.status_code}",
                    sleep_seconds=sleep_seconds,
                    status_code=response.status_code,
                )
                time.sleep(sleep_seconds)
                continue

            response.raise_for_status()
            return self._parse_and_validate_response(
                response=response,
                method=normalized_method,
                raw_path=raw_path,
                actual_path=actual_path,
            )

        # The loop always returns or raises, but keeping this fallback makes the
        # control flow explicit for readers and type checkers.
        if last_response is not None:
            last_response.raise_for_status()
        if last_exception is not None:
            raise last_exception
        raise RuntimeError("Request loop completed without a response or exception.")

    def _render_path(
        self,
        path: str,
        path_params: Mapping[str, Any] | None,
    ) -> str:
        """Substitute `{pathParam}` placeholders with URL-safe values."""

        rendered = path
        for key, value in (path_params or {}).items():
            rendered = rendered.replace("{" + key + "}", quote(str(value), safe=""))

        if "{" in rendered or "}" in rendered:
            raise ValueError(
                f"Unresolved path parameters remain in path: {rendered}"
            )
        return rendered

    def _build_url(self, path: str, *, base_url: str) -> str:
        """Join a selected base URL with a relative API path.

        The client keeps one configured default base URL, but some schema
        families declare different server URLs. Passing the base URL into this
        helper keeps the split of responsibilities obvious: schema matching
        decides which server should be used, and this helper performs the final
        path join.
        """

        normalized_path = path if path.startswith("/") else f"/{path}"
        return f"{base_url.rstrip('/')}{normalized_path}"

    def _build_headers(
        self,
        headers: Mapping[str, str] | None,
        *,
        timeout: float | None,
    ) -> dict[str, str]:
        """Create the final request headers, including Authorization."""

        access_token = self._token_manager.get_access_token(timeout=timeout)
        merged_headers = httpx.Headers(
            {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            }
        )
        if headers:
            for key, value in headers.items():
                merged_headers[key] = value
        return dict(merged_headers)

    def _parse_and_validate_response(
        self,
        *,
        response: httpx.Response,
        method: str,
        raw_path: str,
        actual_path: str,
    ) -> dict[str, Any] | list[Any] | None:
        """Parse a successful response body and validate it against the schema."""

        if response.status_code == 204 or not response.content:
            self._schemas.validate_response(
                method=method,
                raw_path=raw_path,
                actual_path=actual_path,
                status_code=response.status_code,
                payload=None,
            )
            return None

        try:
            payload = response.json()
        except Exception as exc:
            self._logger.error(
                "Response body was not valid JSON.",
                extra={
                    "event": "invalid_json_response",
                    "method": method,
                    "url": str(response.request.url),
                    "path": actual_path,
                    "status_code": response.status_code,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
            raise ValueError(
                f"Expected JSON response for {method} {actual_path}, "
                f"but parsing failed: {exc}"
            ) from exc

        try:
            self._schemas.validate_response(
                method=method,
                raw_path=raw_path,
                actual_path=actual_path,
                status_code=response.status_code,
                payload=payload,
            )
        except ValueError as exc:
            self._logger.error(
                "Response schema validation failed.",
                extra={
                    "event": "schema_validation_failed",
                    "method": method,
                    "url": str(response.request.url),
                    "path": actual_path,
                    "status_code": response.status_code,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
            raise

        return cast(dict[str, Any] | list[Any] | None, payload)

    def _should_retry_response(self, response: httpx.Response) -> bool:
        """Return true for HTTP responses that are safe to retry automatically."""

        return response.status_code in RETRIABLE_STATUS_CODES

    def _retry_delay_from_response(
        self,
        *,
        response: httpx.Response,
        attempt: int,
    ) -> float:
        """Compute the retry delay for a retriable HTTP response."""

        if response.status_code == 429:
            retry_after = self._parse_retry_after(response.headers.get("Retry-After"))
            if retry_after is not None:
                return min(retry_after, self._backoff_max_seconds)
        return self._calculate_backoff(attempt=attempt)

    def _calculate_backoff(self, *, attempt: int) -> float:
        """Apply exponential backoff with jitter using standard library tools."""

        sleep_seconds = min(
            self._backoff_max_seconds,
            self._backoff_base_seconds * (2 ** attempt),
        )
        return float(sleep_seconds * random.uniform(0.75, 1.25))

    def _parse_retry_after(self, value: str | None) -> float | None:
        """Parse either integer or HTTP-date `Retry-After` header values."""

        if value is None:
            return None

        stripped = value.strip()
        if not stripped:
            return None

        if stripped.isdigit():
            return float(stripped)

        try:
            retry_at = parsedate_to_datetime(stripped)
        except (TypeError, ValueError, IndexError):
            return None

        if retry_at.tzinfo is None:
            return None
        return max(0.0, retry_at.timestamp() - time.time())

    def _duration_ms(self, started_at: float) -> int:
        """Convert a monotonic start time into an integer duration in ms."""

        return int((time.monotonic() - started_at) * 1000)

    def _log_request_attempt(
        self,
        *,
        method: str,
        url: str,
        path: str,
        retry_attempt: int,
    ) -> None:
        """Emit a structured log describing an outbound request attempt."""

        self._logger.info(
            "Sending Zoom API request.",
            extra={
                "event": "request_attempt",
                "method": method,
                "url": url,
                "path": path,
                "retry_attempt": retry_attempt,
            },
        )

    def _log_response(
        self,
        *,
        method: str,
        url: str,
        path: str,
        response: httpx.Response,
        duration_ms: int,
    ) -> None:
        """Emit a structured log describing a received HTTP response."""

        self._logger.info(
            "Received Zoom API response.",
            extra={
                "event": "response_received",
                "method": method,
                "url": url,
                "path": path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "request_id": response.headers.get("x-request-id"),
                "trace_id": response.headers.get("x-zm-trackingid"),
            },
        )

    def _log_retry(
        self,
        *,
        method: str,
        url: str,
        path: str,
        retry_attempt: int,
        duration_ms: int,
        reason: str,
        sleep_seconds: float,
        status_code: int | None,
    ) -> None:
        """Emit a structured log describing a retry decision."""

        self._logger.warning(
            "Retrying Zoom API request after a retriable failure.",
            extra={
                "event": "request_retry",
                "method": method,
                "url": url,
                "path": path,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "retry_attempt": retry_attempt,
                "error_type": "retry",
                "error_message": (
                    f"{reason}; sleeping for {sleep_seconds:.2f} seconds"
                ),
            },
        )
