

"""Contract tests for the Zoom SCIM 2.0 endpoints.

Schema location (repo layout):
    src/tests/schemas/accounts/scim2.json

These tests are intentionally implementation-agnostic.

Integration hook
----------------
Provide a `make_client` fixture in your project (usually in `src/tests/conftest.py`) with this shape:

    def make_client(transport: object):
        # Return a Zoom API client wired to use `transport` for HTTP.
        ...

The transport object must provide:

    request(method: str, url: str, *, headers=None, params=None, json=None) -> Response

Where `Response` has:

    status_code: int
    json() -> dict | None

Your client is expected to expose a `.scim2` service with `.users` and `.groups` sub-services.
If your implementation uses different names, adapt in the fixture.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import json

import pytest
from jsonschema import Draft202012Validator


SCHEMA_PATH = Path(__file__).parent / "schemas" / "accounts" / "scim2.json"


# -----------------------------
# Minimal test doubles
# -----------------------------


@dataclass
class FakeResponse:
    status_code: int
    _payload: Optional[dict] = None

    def json(self) -> Optional[dict]:
        return self._payload


class CapturingTransport:
    """A tiny HTTP transport spy.

    Your client should call `transport.request(...)`.
    The tests assert on captured method/url/headers/params/body.
    """

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._queue: List[FakeResponse] = []

    def enqueue(self, response: FakeResponse) -> None:
        self._queue.append(response)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json: Any = None,
    ) -> FakeResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "params": params or {},
                "json": json,
            }
        )
        if not self._queue:
            raise AssertionError(
                "Transport queue empty. Enqueue a FakeResponse before calling the client."
            )
        return self._queue.pop(0)


# -----------------------------
# Schema helpers
# -----------------------------


@pytest.fixture(scope="session")
def scim2_spec() -> dict:
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(
            f"SCIM2 schema not found at {SCHEMA_PATH}. "
            "Expected src/tests/schemas/accounts/scim2.json"
        )
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _response_schema(spec: dict, *, path: str, method: str, status: str) -> dict:
    """Fetch a response schema from the OpenAPI document.

    SCIM specs typically use `application/scim+json`. If the schema uses another JSON content type,
    we fall back to the first JSON-ish content type we find.
    """

    op = spec["paths"][path][method]
    resp = op["responses"][status]
    content = resp.get("content") or {}

    # Prefer SCIM media type, fall back to application/json.
    for ct in ("application/scim+json", "application/json"):
        if ct in content:
            return content[ct]["schema"]

    # Last resort: any +json or json content type.
    for ct, block in content.items():
        if "json" in ct:
            return block["schema"]

    raise KeyError(
        f"No JSON response schema found for {method.upper()} {path} {status}. "
        f"Available content types: {', '.join(content.keys()) if content else '<none>'}"
    )


def _validate(instance: Any, schema: dict) -> None:
    Draft202012Validator(schema).validate(instance)


def _assert_call(
    transport: CapturingTransport,
    *,
    method: str,
    path_suffix: str,
    params: Optional[Dict[str, Any]] = None,
    json_body: Any = None,
) -> None:
    assert transport.calls, "No HTTP calls captured. Your client didn't hit the transport."
    call = transport.calls[-1]

    assert call["method"].upper() == method.upper()
    assert call["url"].endswith(path_suffix)

    if params is not None:
        assert call["params"] == params

    if json_body is not None:
        assert call["json"] == json_body

    # Accept header (optional but nice): SCIM media type or JSON.
    headers = {k.lower(): v for k, v in (call["headers"] or {}).items()}
    accept = headers.get("accept")
    if accept is not None:
        assert ("application/scim+json" in accept) or ("application/json" in accept)


# -----------------------------
# Required integration hook
# -----------------------------


@pytest.fixture
def make_client() -> Callable[[Any], Any]:
    raise RuntimeError(
        "Provide a `make_client(transport)` fixture (e.g., in src/tests/conftest.py) "
        "that returns your Zoom client wired to use the provided transport."
    )


# -----------------------------
# Sample payloads
# -----------------------------


def sample_scim_list(resource_type: str, resources: List[dict]) -> dict:
    return {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": len(resources),
        "startIndex": 1,
        "itemsPerPage": len(resources),
        "Resources": resources,
        "ResourceType": resource_type,
    }


def sample_scim_user(user_id: str = "2819c223-7f76-453a-919d-413861904646") -> dict:
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": user_id,
        "userName": "bjensen@example.com",
        "name": {"givenName": "Barbara", "familyName": "Jensen"},
        "active": True,
        "emails": [{"value": "bjensen@example.com", "type": "work", "primary": True}],
        "meta": {"resourceType": "User"},
    }


def sample_scim_group(group_id: str = "e9e30dba-f08f-4109-8486-d5c6a331660a") -> dict:
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
        "id": group_id,
        "displayName": "Employees",
        "members": [
            {"value": "2819c223-7f76-453a-919d-413861904646", "display": "bjensen"}
        ],
        "meta": {"resourceType": "Group"},
    }


# -----------------------------
# Tests: Users
# -----------------------------


def test_scim2_list_users_builds_correct_request_and_validates_response(
    scim2_spec: dict, make_client: Callable[[Any], Any]
) -> None:
    transport = CapturingTransport()
    client = make_client(transport)

    payload = sample_scim_list("User", [sample_scim_user()])
    transport.enqueue(FakeResponse(200, payload))

    result = client.scim2.users.list(
        start_index=1,
        count=100,
        filter='userName eq "bjensen@example.com"',
    )

    _assert_call(
        transport,
        method="GET",
        path_suffix="/scim2/Users",
        params={"startIndex": 1, "count": 100, "filter": 'userName eq "bjensen@example.com"'},
    )

    schema = _response_schema(scim2_spec, path="/scim2/Users", method="get", status="200")
    _validate(result, schema)


def test_scim2_get_user_builds_correct_request_and_validates_response(
    scim2_spec: dict, make_client: Callable[[Any], Any]
) -> None:
    transport = CapturingTransport()
    client = make_client(transport)

    user_id = "2819c223-7f76-453a-919d-413861904646"
    payload = sample_scim_user(user_id)
    transport.enqueue(FakeResponse(200, payload))

    result = client.scim2.users.get(user_id)

    _assert_call(transport, method="GET", path_suffix=f"/scim2/Users/{user_id}")

    schema = _response_schema(
        scim2_spec,
        path="/scim2/Users/{userId}",
        method="get",
        status="200",
    )
    _validate(result, schema)


def test_scim2_create_user_posts_body_and_validates_response(
    scim2_spec: dict, make_client: Callable[[Any], Any]
) -> None:
    transport = CapturingTransport()
    client = make_client(transport)

    body = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "new.user@example.com",
        "name": {"givenName": "New", "familyName": "User"},
        "emails": [{"value": "new.user@example.com", "type": "work"}],
        "active": True,
    }

    payload = sample_scim_user("11111111-2222-3333-4444-555555555555")
    transport.enqueue(FakeResponse(201, payload))

    result = client.scim2.users.create(body)

    _assert_call(transport, method="POST", path_suffix="/scim2/Users", json_body=body)

    schema = _response_schema(scim2_spec, path="/scim2/Users", method="post", status="201")
    _validate(result, schema)


def test_scim2_replace_user_puts_body_and_validates_response(
    scim2_spec: dict, make_client: Callable[[Any], Any]
) -> None:
    transport = CapturingTransport()
    client = make_client(transport)

    user_id = "2819c223-7f76-453a-919d-413861904646"
    body = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": user_id,
        "userName": "bjensen@example.com",
        "name": {"givenName": "Barbara", "familyName": "Jensen"},
        "active": True,
        "emails": [{"value": "bjensen@example.com", "type": "work", "primary": True}],
    }

    payload = sample_scim_user(user_id)
    transport.enqueue(FakeResponse(200, payload))

    result = client.scim2.users.replace(user_id, body)

    _assert_call(
        transport,
        method="PUT",
        path_suffix=f"/scim2/Users/{user_id}",
        json_body=body,
    )

    schema = _response_schema(
        scim2_spec,
        path="/scim2/Users/{userId}",
        method="put",
        status="200",
    )
    _validate(result, schema)


def test_scim2_patch_user_posts_patchop_and_validates_response(
    scim2_spec: dict, make_client: Callable[[Any], Any]
) -> None:
    transport = CapturingTransport()
    client = make_client(transport)

    user_id = "2819c223-7f76-453a-919d-413861904646"
    patch_body = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "Replace", "path": "active", "value": False}],
    }

    payload = sample_scim_user(user_id)
    payload["active"] = False
    transport.enqueue(FakeResponse(200, payload))

    result = client.scim2.users.patch(user_id, patch_body)

    _assert_call(
        transport,
        method="PATCH",
        path_suffix=f"/scim2/Users/{user_id}",
        json_body=patch_body,
    )

    schema = _response_schema(
        scim2_spec,
        path="/scim2/Users/{userId}",
        method="patch",
        status="200",
    )
    _validate(result, schema)


def test_scim2_delete_user_hits_endpoint_and_returns_none(
    make_client: Callable[[Any], Any]
) -> None:
    transport = CapturingTransport()
    client = make_client(transport)

    user_id = "2819c223-7f76-453a-919d-413861904646"
    transport.enqueue(FakeResponse(204, None))

    result = client.scim2.users.delete(user_id)

    _assert_call(transport, method="DELETE", path_suffix=f"/scim2/Users/{user_id}")
    assert result is None


# -----------------------------
# Tests: Groups
# -----------------------------


def test_scim2_list_groups_builds_correct_request_and_validates_response(
    scim2_spec: dict, make_client: Callable[[Any], Any]
) -> None:
    transport = CapturingTransport()
    client = make_client(transport)

    payload = sample_scim_list("Group", [sample_scim_group()])
    transport.enqueue(FakeResponse(200, payload))

    result = client.scim2.groups.list(
        start_index=1,
        count=50,
        filter='displayName eq "Employees"',
    )

    _assert_call(
        transport,
        method="GET",
        path_suffix="/scim2/Groups",
        params={"startIndex": 1, "count": 50, "filter": 'displayName eq "Employees"'},
    )

    schema = _response_schema(scim2_spec, path="/scim2/Groups", method="get", status="200")
    _validate(result, schema)


def test_scim2_get_group_builds_correct_request_and_validates_response(
    scim2_spec: dict, make_client: Callable[[Any], Any]
) -> None:
    transport = CapturingTransport()
    client = make_client(transport)

    group_id = "e9e30dba-f08f-4109-8486-d5c6a331660a"
    payload = sample_scim_group(group_id)
    transport.enqueue(FakeResponse(200, payload))

    result = client.scim2.groups.get(group_id)

    _assert_call(transport, method="GET", path_suffix=f"/scim2/Groups/{group_id}")

    schema = _response_schema(
        scim2_spec,
        path="/scim2/Groups/{groupId}",
        method="get",
        status="200",
    )
    _validate(result, schema)


def test_scim2_create_group_posts_body_and_validates_response(
    scim2_spec: dict, make_client: Callable[[Any], Any]
) -> None:
    transport = CapturingTransport()
    client = make_client(transport)

    body = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
        "displayName": "Employees",
        "members": [{"value": "2819c223-7f76-453a-919d-413861904646"}],
    }

    payload = sample_scim_group("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    transport.enqueue(FakeResponse(201, payload))

    result = client.scim2.groups.create(body)

    _assert_call(transport, method="POST", path_suffix="/scim2/Groups", json_body=body)

    schema = _response_schema(scim2_spec, path="/scim2/Groups", method="post", status="201")
    _validate(result, schema)


def test_scim2_patch_group_posts_patchop_and_returns_none(
    make_client: Callable[[Any], Any]
) -> None:
    transport = CapturingTransport()
    client = make_client(transport)

    group_id = "e9e30dba-f08f-4109-8486-d5c6a331660a"
    patch_body = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [
            {
                "op": "Add",
                "path": "members",
                "value": [{"value": "2819c223-7f76-453a-919d-413861904646"}],
            }
        ],
    }

    transport.enqueue(FakeResponse(204, None))

    result = client.scim2.groups.patch(group_id, patch_body)

    _assert_call(
        transport,
        method="PATCH",
        path_suffix=f"/scim2/Groups/{group_id}",
        json_body=patch_body,
    )
    assert result is None


def test_scim2_delete_group_hits_endpoint_and_returns_none(
    make_client: Callable[[Any], Any]
) -> None:
    transport = CapturingTransport()
    client = make_client(transport)

    group_id = "e9e30dba-f08f-4109-8486-d5c6a331660a"
    transport.enqueue(FakeResponse(204, None))

    result = client.scim2.groups.delete(group_id)

    _assert_call(transport, method="DELETE", path_suffix=f"/scim2/Groups/{group_id}")
    assert result is None


# -----------------------------
# Error handling (basic contract)
# -----------------------------


@pytest.mark.parametrize(
    "call_factory",
    [
        lambda c: c.scim2.users.list(),
        lambda c: c.scim2.groups.list(),
        lambda c: c.scim2.users.get("2819c223-7f76-453a-919d-413861904646"),
        lambda c: c.scim2.groups.get("e9e30dba-f08f-4109-8486-d5c6a331660a"),
    ],
)
def test_scim2_non_2xx_responses_raise_an_exception(
    make_client: Callable[[Any], Any], call_factory: Callable[[Any], Any]
) -> None:
    transport = CapturingTransport()
    client = make_client(transport)

    transport.enqueue(FakeResponse(401, {"detail": "Unauthorized"}))

    with pytest.raises(Exception):
        call_factory(client)


# -----------------------------
# Bonus: spec sanity checks
# -----------------------------


def test_scim2_openapi_has_expected_paths(scim2_spec: dict) -> None:
    paths = scim2_spec.get("paths", {})
    assert "/scim2/Users" in paths
    assert "/scim2/Users/{userId}" in paths
    assert "/scim2/Groups" in paths
    assert "/scim2/Groups/{groupId}" in paths