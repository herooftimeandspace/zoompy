"""Read-only live integration coverage for `zoom_sdk`.

The unit and contract suites already exercise most code paths offline, but they
cannot prove that a real account can:

1. load credentials from the local environment
2. acquire a live OAuth token
3. execute read-only API requests across more than one schema family
4. validate real response payloads against the bundled schemas
5. return typed SDK models from live data

These tests stay deliberately conservative. Every request uses `GET`, every
list call is bounded with `page_size`, and any missing scope or unprovisioned
product family becomes a skip rather than a failure. The goal is to improve
confidence and integration coverage, not to crawl an account exhaustively.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from zoom_sdk import ZoomClient
from zoom_sdk.config import load_dotenv

REQUIRED_ENV_VARS = (
    "ZOOM_ACCOUNT_ID",
    "ZOOM_CLIENT_ID",
    "ZOOM_CLIENT_SECRET",
)
REPO_ROOT = Path(__file__).resolve().parents[3]
REPO_DOTENV = REPO_ROOT / ".env"


def _load_live_environment() -> None:
    """Load the repository `.env` file without overriding real env vars.

    Integration runs often come from editor tasks or ad hoc shells whose
    current working directory is not the repository root. Resolving the repo
    `.env` file explicitly here keeps local runs predictable while still
    letting exported CI or shell variables take precedence.
    """

    load_dotenv(REPO_DOTENV if REPO_DOTENV.exists() else None)


def _skip_if_credentials_missing() -> None:
    """Skip the suite when the required live credentials are absent."""

    _load_live_environment()
    missing = [name for name in REQUIRED_ENV_VARS if not os.getenv(name)]
    if missing:
        pytest.skip(
            "Integration credentials are missing: " + ", ".join(missing)
        )


def _items(payload: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    """Extract the first matching list of object items from a response payload.

    Zoom list endpoints are not completely consistent about the name of the
    collection field they return. Centralizing the lookup keeps the tests
    readable and avoids sprinkling list-shape heuristics throughout the suite.
    """

    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _first_identifier(item: dict[str, Any], *keys: str) -> str | None:
    """Return the first non-empty identifier value from one resource object."""

    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _resource_id_or_skip(
    items: list[dict[str, Any]],
    *,
    label: str,
    keys: tuple[str, ...],
) -> str:
    """Return one identifier from a provisioned list response or skip cleanly.

    A live account can be valid and authenticated while still having no Zoom
    Phone users or devices provisioned. That should not fail the whole suite,
    because it says more about account shape than client correctness.
    """

    if not items:
        pytest.skip(f"Integration environment has no provisioned {label}.")

    identifier = _first_identifier(items[0], *keys)
    if not identifier:
        pytest.skip(
            f"Integration environment returned {label} data without a stable id."
        )
    return identifier


def _request_or_skip_for_scope(
    client: ZoomClient,
    method: str,
    path: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Execute one live request and skip when the app lacks required scopes.

    In practice, missing scopes are the most common cause of live-read failures
    once credentials are present. Treating them as skips keeps the suite honest:
    the client is functioning, but the environment is not authorized for that
    read-only API family.
    """

    try:
        payload = client.request(method, path, **kwargs)
    except httpx.HTTPStatusError as exc:
        response = exc.response
        message = response.text
        code: int | None = None

        try:
            error_payload = response.json()
        except Exception:
            error_payload = None

        if isinstance(error_payload, dict):
            code_value = error_payload.get("code")
            if isinstance(code_value, int):
                code = code_value
            detail = error_payload.get("message")
            if isinstance(detail, str) and detail:
                message = detail

        if response.status_code in {400, 401, 403} and (
            code == 4711 or "does not contain scopes" in message.lower()
        ):
            pytest.skip(
                f"Integration app is missing required scopes for "
                f"{method} {path}: {message}"
            )
        raise

    assert isinstance(payload, dict)
    return payload


def _get_access_token_or_skip(client: ZoomClient) -> str:
    """Acquire a live token or skip when the environment cannot reach Zoom."""

    try:
        token = client.get_access_token()
    except httpx.TransportError as exc:
        pytest.skip(
            "Integration environment cannot reach Zoom OAuth: "
            f"{type(exc).__name__}: {exc}"
        )
    assert isinstance(token, str)
    assert token
    return token


@pytest.fixture(scope="module")
def live_client() -> Iterator[ZoomClient]:
    """Provide one shared live client for the read-only integration module.

    The module-scoped fixture keeps the suite reasonably fast while still
    exercising real requests and schema validation. We do the minimum bootstrap
    here so individual tests can focus on the specific live path they cover.
    """

    _skip_if_credentials_missing()
    client = ZoomClient()
    try:
        _get_access_token_or_skip(client)
        yield client
    finally:
        client.close()


@pytest.fixture(scope="module")
def users_payload(live_client: ZoomClient) -> dict[str, Any]:
    """Return one bounded live `/users` page."""

    payload = _request_or_skip_for_scope(
        live_client,
        "GET",
        "/users",
        params={"page_size": 10},
    )
    users = _items(payload, "users")
    assert users
    assert len(users) <= 10
    return payload


@pytest.fixture(scope="module")
def user_id(users_payload: dict[str, Any]) -> str:
    """Return one stable user identifier for subsequent detail lookups."""

    users = _items(users_payload, "users")
    identifier = _resource_id_or_skip(
        users,
        label="users",
        keys=("id",),
    )
    return identifier


@pytest.fixture(scope="module")
def phone_users_payload(live_client: ZoomClient) -> dict[str, Any]:
    """Return one bounded live `/phone/users` page or skip cleanly."""

    payload = _request_or_skip_for_scope(
        live_client,
        "GET",
        "/phone/users",
        params={"page_size": 10},
    )
    users = _items(payload, "users", "phone_users")
    if not users:
        pytest.skip("Integration environment has no provisioned phone users.")
    assert len(users) <= 10
    return payload


@pytest.fixture(scope="module")
def phone_user_id(phone_users_payload: dict[str, Any]) -> str:
    """Return one stable Zoom Phone user identifier for detail reads."""

    phone_users = _items(phone_users_payload, "users", "phone_users")
    return _resource_id_or_skip(
        phone_users,
        label="phone users",
        keys=("id", "user_id"),
    )


@pytest.fixture(scope="module")
def phone_devices_payload(live_client: ZoomClient) -> dict[str, Any]:
    """Return one bounded live `/phone/devices` page or skip cleanly."""

    payload = _request_or_skip_for_scope(
        live_client,
        "GET",
        "/phone/devices",
        params={"page_size": 10},
    )
    devices = _items(payload, "devices", "phone_devices")
    if not devices:
        pytest.skip("Integration environment has no provisioned phone devices.")
    assert len(devices) <= 10
    return payload


@pytest.fixture(scope="module")
def phone_device_id(phone_devices_payload: dict[str, Any]) -> str:
    """Return one stable Zoom Phone device identifier for detail reads."""

    devices = _items(phone_devices_payload, "devices", "phone_devices")
    return _resource_id_or_skip(
        devices,
        label="phone devices",
        keys=("id", "device_id"),
    )


@pytest.mark.integration
def test_integration_auth_and_client_bootstrap() -> None:
    """Prove live bootstrap works through the public client surface.

    This test intentionally exercises the context-manager path, token
    acquisition, `.env` loading, and generated SDK namespace discovery in one
    small read-only check.
    """

    _skip_if_credentials_missing()

    with ZoomClient() as client:
        token = _get_access_token_or_skip(client)
        names = dir(client)

    assert token
    assert "users" in names


@pytest.mark.integration
def test_integration_users_list_raw_request(users_payload: dict[str, Any]) -> None:
    """Exercise the low-level request path against a real list endpoint."""

    users = _items(users_payload, "users")
    assert users
    assert len(users) <= 10


@pytest.mark.integration
def test_integration_user_detail_raw_request(
    live_client: ZoomClient,
    user_id: str,
) -> None:
    """Exercise path-parameter rendering and schema validation on user detail."""

    payload = _request_or_skip_for_scope(
        live_client,
        "GET",
        "/users/{userId}",
        path_params={"userId": user_id},
    )

    assert _first_identifier(payload, "id") == user_id


@pytest.mark.integration
def test_integration_users_sdk_typed_list(live_client: ZoomClient) -> None:
    """Use the public SDK path so live coverage reaches the typed layer."""

    payload = live_client.users.list(page_size=10)

    assert isinstance(payload, BaseModel)
    dumped = payload.model_dump(by_alias=True, exclude_none=True)
    users = _items(dumped, "users")
    assert users
    assert len(users) <= 10


@pytest.mark.integration
def test_integration_phone_users_list_raw_request(
    phone_users_payload: dict[str, Any],
) -> None:
    """Read one bounded Zoom Phone user page without mutating any data."""

    phone_users = _items(phone_users_payload, "users", "phone_users")
    assert phone_users
    assert len(phone_users) <= 10


@pytest.mark.integration
def test_integration_phone_user_detail_raw_request(
    live_client: ZoomClient,
    phone_user_id: str,
) -> None:
    """Exercise a second schema family through the low-level request API."""

    payload = _request_or_skip_for_scope(
        live_client,
        "GET",
        "/phone/users/{userId}",
        path_params={"userId": phone_user_id},
    )

    assert _first_identifier(payload, "id", "user_id") == phone_user_id


@pytest.mark.integration
def test_integration_phone_devices_list_raw_request(
    phone_devices_payload: dict[str, Any],
) -> None:
    """Read one bounded Zoom Phone device page safely."""

    devices = _items(phone_devices_payload, "devices", "phone_devices")
    assert devices
    assert len(devices) <= 10


@pytest.mark.integration
def test_integration_phone_device_detail_raw_request(
    live_client: ZoomClient,
    phone_device_id: str,
) -> None:
    """Exercise one read-only phone-device detail lookup."""

    payload = _request_or_skip_for_scope(
        live_client,
        "GET",
        "/phone/devices/{deviceId}",
        path_params={"deviceId": phone_device_id},
    )

    assert _first_identifier(payload, "id", "device_id") == phone_device_id
