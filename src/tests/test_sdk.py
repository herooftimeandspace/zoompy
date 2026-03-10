"""Focused tests for the dynamic SDK layer built on top of `ZoomClient`.

The repository already has broad contract coverage for the low-level
`request()` method. These tests stay intentionally narrow on the new ergonomic
surface so future maintainers can answer one simple question quickly:

"When I call `client.users.get(...)`, does it map to the right underlying
request shape?"

Using a tiny temporary schema tree keeps the tests readable and avoids coupling
the SDK behavior checks to the full Zoom schema corpus.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel, ValidationError

from zoompy import ZoomClient
from zoompy.schema import SchemaRegistry


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write one small schema document into a temporary resource tree."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _build_sdk_client(tmp_path: Path) -> ZoomClient:
    """Create a client backed by a tiny schema tree tailored for SDK tests.

    The schema includes the classic collection/detail pattern that most callers
    expect from an SDK:

    * `GET /users` -> `client.users.list(...)`
    * `POST /users` -> `client.users.create(...)`
    * `GET /users/{userId}` -> `client.users.get(...)`
    * `GET /phone/users/{userId}` -> `client.phone.users.get(...)`
    """

    _write_json(
        tmp_path / "endpoints" / "accounts" / "Users.json",
        {
            "openapi": "3.0.0",
            "info": {"title": "Users"},
            "servers": [{"url": "https://api.zoom.us/v2"}],
            "paths": {
                "/users": {
                    "get": {
                        "operationId": "listUsers",
                        "summary": "List users",
                        "parameters": [
                            {
                                "name": "page_size",
                                "in": "query",
                                "required": False,
                                "schema": {"type": "integer"},
                            }
                        ],
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "users": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "userId": {
                                                                "type": "string"
                                                            },
                                                            "displayName": {
                                                                "type": "string"
                                                            },
                                                        },
                                                        "required": ["userId"],
                                                    },
                                                }
                                            },
                                            "required": ["users"],
                                        }
                                    }
                                }
                            }
                        },
                    },
                    "post": {
                        "operationId": "createUser",
                        "summary": "Create user",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "email": {"type": "string"},
                                            "firstName": {"type": "string"},
                                        },
                                        "required": ["email"],
                                    }
                                }
                            }
                        },
                        "responses": {
                            "201": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "string"},
                                                "email": {"type": "string"},
                                            },
                                            "required": ["id", "email"],
                                        }
                                    }
                                }
                            }
                        },
                    },
                },
                "/users/{userId}": {
                    "get": {
                        "operationId": "getUser",
                        "summary": "Get user",
                        "parameters": [
                            {
                                "name": "userId",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "userId": {"type": "string"},
                                                "displayName": {"type": "string"},
                                            },
                                            "required": ["userId"],
                                        }
                                    }
                                }
                            }
                        },
                    }
                },
            },
        },
    )
    _write_json(
        tmp_path / "endpoints" / "workplace" / "Phone.json",
        {
            "openapi": "3.0.0",
            "info": {"title": "Phone"},
            "servers": [{"url": "https://api.zoom.us/v2"}],
            "paths": {
                "/phone/users": {
                    "get": {
                        "operationId": "listPhoneUsers",
                        "summary": "List phone users",
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "userId": {"type": "string"},
                                                "extensionNumber": {
                                                    "type": "string"
                                                },
                                            },
                                            "required": ["userId"],
                                        }
                                    }
                                }
                            }
                        },
                    }
                },
                "/phone/users/{userId}": {
                    "get": {
                        "operationId": "getPhoneUser",
                        "summary": "Get phone user",
                        "parameters": [
                            {
                                "name": "userId",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            },
                            {
                                "name": "includeInactive",
                                "in": "query",
                                "required": False,
                                "schema": {"type": "boolean"},
                            },
                        ],
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "userId": {"type": "string"},
                                                "displayName": {
                                                    "type": "string"
                                                },
                                            },
                                            "required": ["userId"],
                                        }
                                    }
                                }
                            }
                        },
                    }
                },
            },
        },
    )

    return ZoomClient(
        access_token="test-access-token",
        schema_registry=SchemaRegistry(resource_root=tmp_path),
    )


def test_zoom_client_exposes_generated_service_namespaces(tmp_path: Path) -> None:
    """Expose schema-derived namespaces directly from the client object."""

    client = _build_sdk_client(tmp_path)
    try:
        assert callable(client.users.list)
        assert callable(client.users.get)
        assert callable(client.users.create)
        assert callable(client.phone.users.get)
        assert callable(client.phone.user.get)
    finally:
        client.close()


def test_sdk_list_alias_maps_kwargs_to_query_params(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treat leftover keyword arguments as query parameters by default."""

    client = _build_sdk_client(tmp_path)
    recorded: dict[str, Any] = {}

    def fake_request(
        method: str,
        path: str,
        *,
        path_params: Any = None,
        params: Any = None,
        json: Any = None,
        headers: Any = None,
        timeout: Any = None,
    ) -> dict[str, bool]:
        recorded.update(
            {
                "method": method,
                "path": path,
                "path_params": path_params,
                "params": params,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return {"ok": True}

    monkeypatch.setattr(client, "request", fake_request)

    try:
        result = client.users.list.raw(page_size=10, status="active")
    finally:
        client.close()

    assert result == {"ok": True}
    assert recorded == {
        "method": "GET",
        "path": "/users",
        "path_params": None,
        "params": {"page_size": 10, "status": "active"},
        "json": None,
        "headers": None,
        "timeout": None,
    }


def test_sdk_get_alias_maps_snake_case_path_parameters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Convert Pythonic parameter names back into the OpenAPI placeholder map."""

    client = _build_sdk_client(tmp_path)
    recorded: dict[str, Any] = {}

    def fake_request(
        method: str,
        path: str,
        *,
        path_params: Any = None,
        params: Any = None,
        json: Any = None,
        headers: Any = None,
        timeout: Any = None,
    ) -> dict[str, bool]:
        recorded.update(
            {
                "method": method,
                "path": path,
                "path_params": path_params,
                "params": params,
            }
        )
        return {"ok": True}

    monkeypatch.setattr(client, "request", fake_request)

    try:
        result = client.phone.users.get.raw(
            user_id="user-123",
            include_inactive=True,
        )
    finally:
        client.close()

    assert result == {"ok": True}
    assert recorded == {
        "method": "GET",
        "path": "/phone/users/{userId}",
        "path_params": {"userId": "user-123"},
        "params": {"include_inactive": True},
    }


def test_sdk_operation_id_method_and_create_alias_forward_json_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expose both CRUD aliases and snake-cased operation-id methods.

    The CRUD aliases are nice for common resource shapes, but the operation-id
    methods are the stable escape hatch for unusual Zoom paths that do not map
    cleanly onto a tiny CRUD vocabulary.
    """

    client = _build_sdk_client(tmp_path)
    recorded: list[dict[str, Any]] = []

    def fake_request(
        method: str,
        path: str,
        *,
        path_params: Any = None,
        params: Any = None,
        json: Any = None,
        headers: Any = None,
        timeout: Any = None,
    ) -> dict[str, bool]:
        recorded.append(
            {
                "method": method,
                "path": path,
                "path_params": path_params,
                "params": params,
                "json": json,
            }
        )
        return {"ok": True}

    monkeypatch.setattr(client, "request", fake_request)

    try:
        client.users.create.raw(email="person@example.com")
        client.users.create_user.raw(json={"email": "person@example.com"})
    finally:
        client.close()

    assert recorded == [
        {
            "method": "POST",
            "path": "/users",
            "path_params": None,
            "params": None,
            "json": {"email": "person@example.com"},
        },
        {
            "method": "POST",
            "path": "/users",
            "path_params": None,
            "params": None,
            "json": {"email": "person@example.com"},
        },
    ]


def test_sdk_requires_missing_path_parameters_explicitly(
    tmp_path: Path,
) -> None:
    """Fail fast when a generated detail method is missing a path value."""

    client = _build_sdk_client(tmp_path)
    try:
        with pytest.raises(TypeError, match="user_id"):
            client.users.get()
    finally:
        client.close()


def test_sdk_exposes_typed_request_and_response_models(tmp_path: Path) -> None:
    """Still expose the generated models for advanced callers who want them."""

    client = _build_sdk_client(tmp_path)
    try:
        request_model = client.users.create.request_model
        response_model = client.users.get.response_model
    finally:
        client.close()

    assert request_model is not None
    assert response_model is not None
    assert issubclass(request_model, BaseModel)
    assert issubclass(response_model, BaseModel)


def test_sdk_calls_return_model_instances_with_pythonic_fields_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return typed models by default instead of requiring `.typed(...)`."""

    client = _build_sdk_client(tmp_path)

    def fake_request(
        method: str,
        path: str,
        *,
        path_params: Any = None,
        params: Any = None,
        json: Any = None,
        headers: Any = None,
        timeout: Any = None,
    ) -> dict[str, Any]:
        return {
            "userId": path_params["userId"],
            "displayName": "Ada Lovelace",
        }

    monkeypatch.setattr(client, "request", fake_request)

    try:
        result = client.users.get(user_id="me")
    finally:
        client.close()

    assert isinstance(result, BaseModel)
    typed_result = cast(Any, result)
    assert typed_result.user_id == "me"
    assert typed_result.display_name == "Ada Lovelace"


def test_sdk_validates_request_bodies_before_sending_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treat leftover kwargs as request-body fields for body operations."""

    client = _build_sdk_client(tmp_path)
    recorded: dict[str, Any] = {}

    def fake_request(
        method: str,
        path: str,
        *,
        path_params: Any = None,
        params: Any = None,
        json: Any = None,
        headers: Any = None,
        timeout: Any = None,
    ) -> dict[str, Any]:
        recorded["json"] = json
        return {"id": "abc123", "email": json["email"]}

    monkeypatch.setattr(client, "request", fake_request)

    try:
        response = client.users.create(
            email="person@example.com",
            first_name="Ada",
        )
    finally:
        client.close()

    assert recorded["json"] == {
        "email": "person@example.com",
        "firstName": "Ada",
    }
    assert isinstance(response, BaseModel)
    typed_response = cast(Any, response)
    assert typed_response.email == "person@example.com"


def test_sdk_rejects_invalid_request_bodies_by_default(
    tmp_path: Path,
) -> None:
    """Raise a validation error before dispatching an invalid body payload."""

    client = _build_sdk_client(tmp_path)
    try:
        with pytest.raises(ValidationError, match="email"):
            client.users.create(first_name="Ada")
    finally:
        client.close()


def test_sdk_supports_singular_namespace_and_generic_id_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allow `phone.user.get(id=...)` as a friendlier scripting shorthand."""

    client = _build_sdk_client(tmp_path)
    recorded: dict[str, Any] = {}

    def fake_request(
        method: str,
        path: str,
        *,
        path_params: Any = None,
        params: Any = None,
        json: Any = None,
        headers: Any = None,
        timeout: Any = None,
    ) -> dict[str, Any]:
        recorded["path_params"] = path_params
        return {"userId": path_params["userId"]}

    monkeypatch.setattr(client, "request", fake_request)

    try:
        result = client.phone.user.get(id="1234")
    finally:
        client.close()

    assert isinstance(result, BaseModel)
    typed_result = cast(Any, result)
    assert typed_result.user_id == "1234"
    assert recorded["path_params"] == {"userId": "1234"}
