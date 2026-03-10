"""Minimal live integration smoke tests for `zoompy`.

The contract suites cover most behavior offline with mocked HTTP. This file is
deliberately small and exists to prove that live configuration can do three
useful things against a real Zoom account:

1. acquire an OAuth access token
2. read a few representative user-management endpoints
3. read a few representative Zoom Phone endpoints

The integration suite must remain non-destructive. Every request here uses
`GET`, and the test only reads existing resources from the environment under
test.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest

from zoompy import ZoomClient
from zoompy.config import load_dotenv

REQUIRED_ENV_VARS = (
    "ZOOM_ACCOUNT_ID",
    "ZOOM_CLIENT_ID",
    "ZOOM_CLIENT_SECRET",
)


def _skip_if_credentials_missing() -> None:
    """Skip the test when the required live Zoom credentials are absent."""

    # Integration tests should honor the same local `.env` behavior as the
    # client itself. Loading it here lets a developer run the live smoke test
    # without exporting every credential into their shell first.
    load_dotenv()
    missing = [name for name in REQUIRED_ENV_VARS if not os.getenv(name)]
    if missing:
        pytest.skip(
            "Integration credentials are missing: " + ", ".join(missing)
        )


def _items(payload: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    """Extract the first matching list of objects from a response payload.

    Zoom list endpoints are not perfectly consistent about the collection key
    they use. This helper keeps the smoke test readable by centralizing the
    "look for the obvious list field names" logic in one place.
    """

    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _first_identifier(item: dict[str, Any], *keys: str) -> str | None:
    """Return the first non-empty string identifier from a resource object."""

    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _request_or_skip_for_scope(
    client: ZoomClient,
    method: str,
    path: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run one live request and skip cleanly when the token lacks scopes.

    In live environments, the most common "client looks broken" failure is
    actually an under-scoped Server-to-Server OAuth app. Zoom reports that as
    an HTTP error with a JSON body explaining which scopes are missing. Turning
    that into an explicit skip makes the smoke test much easier to interpret:
    the environment is reachable and authenticated, but the app is not yet
    authorized for the requested read-only endpoint.
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
    """Acquire a live token or skip when the environment cannot reach Zoom.

    Integration tests are meant to verify real connectivity when it exists, but
    local and CI environments do not always have outbound network access. A
    transport-level connection failure is therefore a test-environment issue,
    not evidence that the client contract is broken.
    """

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


@pytest.mark.integration
def test_integration_smoke_read_only_endpoints() -> None:
    """Exercise a small read-only slice of the real Zoom API.

    The intent is not to prove that every endpoint family works live. The
    intent is to validate that the client can:

    - authenticate successfully
    - perform ordinary account-level reads
    - reach a second product family (`/phone/...`) through the same client
    - resolve and validate real responses from multiple schemas
    """

    _skip_if_credentials_missing()

    client = ZoomClient()
    try:
        _get_access_token_or_skip(client)

        # Read a bounded page of users so the smoke test stays fast and
        # non-destructive even on large accounts.
        users_payload = _request_or_skip_for_scope(
            client,
            "GET",
            "/users",
            params={"page_size": 10},
        )
        users = _items(users_payload, "users")
        assert users
        assert len(users) <= 10

        # Pull phone users as a second live API family. Not every account will
        # have Zoom Phone resources; when the endpoint returns no users, we skip
        # the phone-specific detail checks rather than failing the whole smoke
        # test for lack of provisioned data.
        phone_users_payload = _request_or_skip_for_scope(
            client,
            "GET",
            "/phone/users",
            params={"page_size": 10},
        )
        phone_users = _items(phone_users_payload, "users", "phone_users")
        assert phone_users

        # Prefer one identifier that can be used against both user endpoints so
        # we verify the same logical account entity from two API families.
        user_ids = {
            identifier
            for user in users
            if (identifier := _first_identifier(user, "id"))
        }
        phone_user_ids = {
            identifier
            for user in phone_users
            if (identifier := _first_identifier(user, "id", "user_id"))
        }

        shared_user_id = next(iter(user_ids & phone_user_ids), None)
        generic_user_id = shared_user_id or _first_identifier(users[0], "id")
        phone_user_id = shared_user_id or _first_identifier(
            phone_users[0],
            "id",
            "user_id",
        )

        assert generic_user_id
        assert phone_user_id

        _request_or_skip_for_scope(
            client,
            "GET",
            "/users/{userId}",
            path_params={"userId": generic_user_id},
        )

        _request_or_skip_for_scope(
            client,
            "GET",
            "/phone/users/{userId}",
            path_params={"userId": phone_user_id},
        )

        # We use Zoom Phone devices here as the concrete "phones" collection
        # because the schema includes both list and detail GET endpoints for the
        # same resource type.
        phone_devices_payload = _request_or_skip_for_scope(
            client,
            "GET",
            "/phone/devices",
            params={"page_size": 10},
        )
        phone_devices = _items(
            phone_devices_payload,
            "devices",
            "phone_devices",
        )
        assert phone_devices

        phone_device_id = _first_identifier(phone_devices[0], "id", "device_id")
        assert phone_device_id

        _request_or_skip_for_scope(
            client,
            "GET",
            "/phone/devices/{deviceId}",
            path_params={"deviceId": phone_device_id},
        )
    finally:
        client.close()
