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

import inspect
import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel, ValidationError

from zoom_sdk import ZoomClient
from zoom_sdk.schema import SchemaRegistry


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write one small schema document into a temporary resource tree."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _build_sdk_client(
    tmp_path: Path,
    *,
    account_id: str | None = None,
) -> ZoomClient:
    """Create a client backed by a tiny schema tree tailored for SDK tests.

    The schema includes the classic collection/detail pattern that most callers
    expect from an SDK:

    * `GET /users` -> `client.users.list(...)`
    * `POST /users` -> `client.users.create(...)`
    * `GET /users/{userId}` -> `client.users.get(...)`
    * `GET /phone/users/{userId}` -> `client.phone.users.get(...)`
    * `GET /accounts/{accountId}/phone/common_areas` ->
      `client.accounts.account_id.phone.common_areas.list(...)`
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
                                "description": "Maximum number of users to return.",
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
                                "description": "The Zoom user identifier.",
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
                                "description": "The Zoom Phone user identifier.",
                                "schema": {"type": "string"},
                            },
                            {
                                "name": "includeInactive",
                                "in": "query",
                                "required": False,
                                "description": "Whether inactive users should be included.",
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
                    },
                    "patch": {
                        "operationId": "updateUserProfile",
                        "summary": "Update phone user profile",
                        "parameters": [
                            {
                                "name": "userId",
                                "in": "path",
                                "required": True,
                                "description": "The Zoom Phone user identifier.",
                                "schema": {"type": "string"},
                            }
                        ],
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "displayName": {"type": "string"},
                                        },
                                        "required": ["displayName"],
                                    }
                                }
                            }
                        },
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
                "/accounts/{accountId}/phone/common_areas": {
                    "get": {
                        "operationId": "listCommonAreas",
                        "summary": "List common areas",
                        "parameters": [
                            {
                                "name": "accountId",
                                "in": "path",
                                "required": True,
                                "description": "The account identifier.",
                                "schema": {"type": "string"},
                            },
                            {
                                "name": "page_size",
                                "in": "query",
                                "required": False,
                                "description": "Maximum number of common areas to return.",
                                "schema": {"type": "integer"},
                            },
                        ],
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "commonAreas": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "commonAreaId": {
                                                                "type": "string"
                                                            }
                                                        },
                                                        "required": [
                                                            "commonAreaId"
                                                        ],
                                                    },
                                                }
                                            },
                                            "required": ["commonAreas"],
                                        }
                                    }
                                }
                            }
                        },
                    }
                },
                "/accounts/{accountId}/phone/common_areas/{commonAreaId}": {
                    "get": {
                        "operationId": "getCommonArea",
                        "summary": "Get common area",
                        "parameters": [
                            {
                                "name": "accountId",
                                "in": "path",
                                "required": True,
                                "description": "The account identifier.",
                                "schema": {"type": "string"},
                            },
                            {
                                "name": "commonAreaId",
                                "in": "path",
                                "required": True,
                                "description": "The common area identifier.",
                                "schema": {"type": "string"},
                            },
                        ],
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "commonAreaId": {
                                                    "type": "string"
                                                }
                                            },
                                            "required": ["commonAreaId"],
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
        account_id=account_id,
        access_token="test-access-token",
        load_dotenv=False,
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
        assert callable(client.phone.users.update_profile)
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


def test_sdk_raw_body_method_reuses_pythonic_argument_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forward the same Python SDK arguments through the bounded byte path."""

    client = _build_sdk_client(tmp_path)
    recorded: dict[str, Any] = {}

    def fake_request_raw_body(
        method: str,
        path: str,
        *,
        path_params: Any = None,
        params: Any = None,
        json: Any = None,
        headers: Any = None,
        timeout: Any = None,
    ) -> bytes:
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
        return b'{"users":[]}'

    monkeypatch.setattr(client, "request_raw_body", fake_request_raw_body)

    try:
        result = client.users.list.raw_body(
            page_size=10,
            headers={"X-Compatibility-Mode": "safe"},
            timeout=2.5,
        )
    finally:
        client.close()

    assert result == b'{"users":[]}'
    assert recorded == {
        "method": "GET",
        "path": "/users",
        "path_params": None,
        "params": {"page_size": 10},
        "json": None,
        "headers": {"X-Compatibility-Mode": "safe"},
        "timeout": 2.5,
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


def test_sdk_account_scoped_list_uses_client_default_account_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fill missing account-scoped list path params from the client default."""

    client = _build_sdk_client(tmp_path, account_id="acct-123")
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
        result = client.accounts.account_id.phone.common_areas.list.raw(page_size=100)
    finally:
        client.close()

    assert client.default_account_id == "acct-123"
    assert result == {"ok": True}
    assert recorded == {
        "method": "GET",
        "path": "/accounts/{accountId}/phone/common_areas",
        "path_params": {"accountId": "acct-123"},
        "params": {"page_size": 100},
        "json": None,
        "headers": None,
        "timeout": None,
    }


def test_sdk_account_scoped_detail_uses_client_default_account_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fill omitted account ids for account-scoped detail calls as well."""

    client = _build_sdk_client(tmp_path, account_id="acct-123")
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
        result = client.accounts.account_id.phone.common_areas.get.raw(
            common_area_id="ca-1"
        )
    finally:
        client.close()

    assert result == {"ok": True}
    assert recorded == {
        "method": "GET",
        "path": "/accounts/{accountId}/phone/common_areas/{commonAreaId}",
        "path_params": {"accountId": "acct-123", "commonAreaId": "ca-1"},
        "params": None,
        "json": None,
        "headers": None,
        "timeout": None,
    }


@pytest.mark.parametrize(
    ("override_kwargs", "expected_path_params"),
    [
        ({"account_id": "acct-999"}, {"accountId": "acct-999"}),
        ({"accountId": "acct-998"}, {"accountId": "acct-998"}),
        ({"path_params": {"accountId": "acct-997"}}, {"accountId": "acct-997"}),
    ],
)
def test_sdk_account_scoped_methods_preserve_explicit_account_id_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    override_kwargs: dict[str, Any],
    expected_path_params: dict[str, str],
) -> None:
    """Keep explicit caller-provided account ids ahead of client defaults."""

    client = _build_sdk_client(tmp_path, account_id="acct-123")
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
        result = client.accounts.account_id.phone.common_areas.list.raw(
            page_size=100,
            **override_kwargs,
        )
    finally:
        client.close()

    assert result == {"ok": True}
    assert recorded == {
        "method": "GET",
        "path": "/accounts/{accountId}/phone/common_areas",
        "path_params": expected_path_params,
        "params": {"page_size": 100},
    }


def test_sdk_account_scoped_methods_still_require_account_id_without_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the existing missing-path-parameter failure without a default."""

    monkeypatch.delenv("ZOOM_ACCOUNT_ID", raising=False)
    client = _build_sdk_client(tmp_path)
    try:
        with pytest.raises(TypeError, match="account_id"):
            client.accounts.account_id.phone.common_areas.list.raw(page_size=100)
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


def test_sdk_methods_expose_schema_derived_signatures(tmp_path: Path) -> None:
    """Expose useful parameter and return types through `inspect.signature`.

    A new user should be able to hover a method in an editor and see enough
    guidance to build a valid request without reading the implementation first.
    """

    client = _build_sdk_client(tmp_path)
    try:
        get_signature = str(inspect.signature(client.phone.users.get))
        create_signature = str(inspect.signature(client.users.create))
        phone_response_model = client.phone.users.get.response_model
    finally:
        client.close()

    assert get_signature.startswith(
        "(*, user_id: str, include_inactive: bool | None = None, "
        "headers: collections.abc.Mapping[str, str] | None = None, "
        "timeout: float | None = None) -> "
    )
    assert get_signature.endswith(f"{phone_response_model.__name__} | None")
    assert "body:" in create_signature
    assert "**body_fields: Any" in create_signature


def test_sdk_docstrings_include_types_and_request_guidance(tmp_path: Path) -> None:
    """Document parameter types and body hints directly on generated methods."""

    client = _build_sdk_client(tmp_path)
    try:
        get_docstring = client.phone.users.get.__doc__
        create_docstring = client.users.create.__doc__
    finally:
        client.close()

    assert get_docstring is not None
    assert "Python signature:" in get_docstring
    assert "user_id: str" in get_docstring
    assert "include_inactive: bool | None" in get_docstring
    assert "The Zoom Phone user identifier." in get_docstring

    assert create_docstring is not None
    assert "Request body:" in create_docstring
    assert "top-level body fields:" in create_docstring
    assert "email: str (required)" in create_docstring
    assert "first_name: str | None (optional)" in create_docstring
    assert "Tooling hints:" in create_docstring


def test_sdk_calls_return_model_instances_with_pythonic_fields_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return typed models by default from normal SDK calls."""

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
        _ = (method, path, params, json, headers, timeout)
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


def test_sdk_rejects_invalid_typed_response_payloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject malformed response payloads before exposing typed SDK results.

    The lower-level request layer already validates live responses against the
    OpenAPI documents. This test protects the SDK layer itself: if a mocked or
    otherwise malformed payload still reaches the typed method wrapper, the
    generated response model should refuse to coerce it into a seemingly valid
    object.
    """

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
        _ = (method, path, path_params, params, json, headers, timeout)
        return {
            "displayName": "Ada Lovelace",
        }

    monkeypatch.setattr(client, "request", fake_request)

    try:
        with pytest.raises(ValidationError, match="userId"):
            client.users.get(user_id="me")
    finally:
        client.close()


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
        _ = (method, path, path_params, params, headers, timeout)
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


def test_sdk_supports_singular_namespaces_with_schema_native_parameters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allow singular namespace aliases while keeping schema-native params."""

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
        _ = (method, path, params, json, headers, timeout)
        recorded["path_params"] = path_params
        return {"userId": path_params["userId"]}

    monkeypatch.setattr(client, "request", fake_request)

    try:
        result = client.phone.user.get(user_id="1234")
    finally:
        client.close()

    assert isinstance(result, BaseModel)
    typed_result = cast(Any, result)
    assert typed_result.user_id == "1234"
    assert recorded["path_params"] == {"userId": "1234"}


def test_sdk_rejects_non_schema_generic_id_shorthand(tmp_path: Path) -> None:
    """Reject generic aliases that are not present in the schema contract."""

    client = _build_sdk_client(tmp_path)
    try:
        with pytest.raises(TypeError, match="user_id"):
            client.phone.users.get(id="1234")
    finally:
        client.close()


def test_sdk_semantic_aliases_expose_cleaner_operation_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Derive useful snake-cased aliases from complex operation ids."""

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
        _ = (params, headers, timeout)
        recorded.update(
            {
                "method": method,
                "path": path,
                "path_params": path_params,
                "json": json,
            }
        )
        return {"userId": path_params["userId"], "displayName": json["displayName"]}

    monkeypatch.setattr(client, "request", fake_request)

    try:
        result = client.phone.users.update_profile(
            user_id="1234",
            display_name="Ada Lovelace",
        )
    finally:
        client.close()

    assert isinstance(result, BaseModel)
    typed_result = cast(Any, result)
    assert typed_result.display_name == "Ada Lovelace"
    assert recorded == {
        "method": "PATCH",
        "path": "/phone/users/{userId}",
        "path_params": {"userId": "1234"},
        "json": {"displayName": "Ada Lovelace"},
    }


def test_sdk_iter_pages_follows_next_page_tokens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Walk paginated list endpoints one typed page at a time."""

    client = _build_sdk_client(tmp_path)
    calls: list[dict[str, Any]] = []
    responses = [
        {
            "users": [{"userId": "u1"}],
            "next_page_token": "token-2",
        },
        {
            "users": [{"userId": "u2"}],
            "next_page_token": "",
        },
    ]

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
        _ = (path_params, json, headers, timeout)
        calls.append({"method": method, "path": path, "params": params})
        return responses[len(calls) - 1]

    monkeypatch.setattr(client, "request", fake_request)

    try:
        pages = list(client.users.list.iter_pages(page_size=1))
    finally:
        client.close()

    assert len(pages) == 2
    assert calls == [
        {"method": "GET", "path": "/users", "params": {"page_size": 1}},
        {
            "method": "GET",
            "path": "/users",
            "params": {"page_size": 1, "next_page_token": "token-2"},
        },
    ]


def test_sdk_iter_all_yields_collection_items_across_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flatten paginated collection responses into one item stream."""

    client = _build_sdk_client(tmp_path)
    responses = [
        {
            "users": [{"userId": "u1"}, {"userId": "u2"}],
            "next_page_token": "token-2",
        },
        {
            "users": [{"userId": "u3"}],
            "next_page_token": "",
        },
    ]
    call_count = 0

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
        _ = (method, path, path_params, params, json, headers, timeout)
        nonlocal call_count
        call_count += 1
        return responses[call_count - 1]

    monkeypatch.setattr(client, "request", fake_request)

    try:
        items = list(client.users.list.iter_all(page_size=2))
    finally:
        client.close()

    assert [cast(Any, item).user_id for item in items] == ["u1", "u2", "u3"]


def test_sdk_paginate_exposes_page_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expose pagination metadata without forcing callers to parse raw pages."""

    client = _build_sdk_client(tmp_path)
    responses = [
        {
            "users": [{"userId": "u1"}],
            "next_page_token": "token-2",
            "page_size": 1,
            "total_records": 2,
        },
        {
            "users": [{"userId": "u2"}],
            "next_page_token": "",
            "page_size": 1,
            "total_records": 2,
        },
    ]
    call_count = 0

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
        _ = (method, path, path_params, params, json, headers, timeout)
        nonlocal call_count
        call_count += 1
        return responses[call_count - 1]

    monkeypatch.setattr(client, "request", fake_request)

    try:
        pages = list(client.users.list.paginate(page_size=1))
    finally:
        client.close()

    assert len(pages) == 2
    assert [cast(Any, item).user_id for item in pages[0].items] == ["u1"]
    assert pages[0].next_page_token == "token-2"
    assert pages[0].page_size == 1
    assert pages[0].total_records == 2
