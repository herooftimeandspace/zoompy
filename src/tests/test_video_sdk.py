

"""Contract tests for the Zoom Video SDK REST API endpoints.

These tests are intentionally schema-driven: they load the OpenAPI document in
`src/tests/schemas/build_platform/Video SDK.json` and validate that an API client
implementation:

1) Exposes a `video_sdk` endpoint group.
2) Provides callables for every operation in the OpenAPI spec.
3) Sends the correct HTTP method + URL path.
4) Enforces required parameters (query/path/body) before making a request.
5) Returns JSON that matches the documented success-response schema.

If these tests feel demanding, that's because APIs are demanding. You're welcome.
"""

from __future__ import annotations

import inspect
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

import httpx
import pytest
import respx

try:
    import jsonschema
except Exception as e:  # pragma: no cover
    jsonschema = None  # type: ignore


SCHEMA_PATH = Path("src/tests/schemas/build_platform/Video SDK.json")


# -------------------------
# Helpers: OpenAPI handling
# -------------------------

def _load_spec() -> dict:
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(
            f"Missing OpenAPI schema at {SCHEMA_PATH}. "
            "Make sure you copied the provided 'Video SDK.json' into that path."
        )
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _snake_case(name: str) -> str:
    # Handles CamelCase, lowerCamelCase, and the occasional chaotic operationId.
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return re.sub(r"__+", "_", s).strip("_").lower()


def _iter_operations(spec: dict) -> Iterable[Tuple[str, str, str, dict]]:
    """Yield (operation_id, http_method, path, operation_obj)."""
    for path, path_item in spec.get("paths", {}).items():
        for http_method, op in path_item.items():
            if http_method.startswith("x-"):
                continue
            operation_id = op.get("operationId")
            if not operation_id:
                continue
            yield operation_id, http_method.lower(), path, op


def _pick_success_response(op: dict) -> Optional[str]:
    """Pick a "success" HTTP status code (prefer 200, else any 2xx)."""
    codes = [str(c) for c in op.get("responses", {}).keys()]
    success = [c for c in codes if c.startswith("2")]
    if not success:
        return None
    if "200" in success:
        return "200"
    # Prefer 201 then 204 then the rest.
    for preferred in ("201", "204"):
        if preferred in success:
            return preferred
    return sorted(success)[0]


def _resolve_ref(spec: dict, ref: str) -> dict:
    if not ref.startswith("#/"):
        raise ValueError(f"Only local $ref values are supported in tests: {ref}")
    node: Any = spec
    for part in ref.lstrip("#/").split("/"):
        node = node[part]
    if not isinstance(node, dict):
        raise TypeError(f"Resolved ref is not an object: {ref}")
    return node


def _schema_to_example(spec: dict, schema: dict) -> Any:
    """Create a minimal example instance that satisfies a (simple) OpenAPI schema."""
    if "$ref" in schema:
        return _schema_to_example(spec, _resolve_ref(spec, schema["$ref"]))

    # Prefer explicit example/default/enum.
    if "example" in schema:
        return schema["example"]
    if "default" in schema:
        return schema["default"]
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]

    t = schema.get("type")

    if t == "object" or ("properties" in schema and t is None):
        props: dict = schema.get("properties", {})
        required = set(schema.get("required", []))
        out: Dict[str, Any] = {}
        for k in required:
            out[k] = _schema_to_example(spec, props.get(k, {}))
        # If required is empty, include 1 property to avoid returning {} for things
        # that are meant to have a shape.
        if not out and props:
            k = next(iter(props.keys()))
            out[k] = _schema_to_example(spec, props[k])
        return out

    if t == "array":
        items = schema.get("items", {})
        return [_schema_to_example(spec, items)]

    if t == "string":
        fmt = schema.get("format")
        if fmt == "date":
            return "2021-12-01"
        if fmt == "date-time":
            return "2021-12-01T00:00:00Z"
        if fmt == "email":
            return "test@example.com"
        return "string"

    if t == "integer":
        return int(schema.get("example", 1))

    if t == "number":
        return float(schema.get("example", 1.0))

    if t == "boolean":
        return True

    # Fallback: some schemas omit type.
    return schema.get("example", {})


def _build_request_kwargs(spec: dict, path: str, op: dict) -> Tuple[dict, dict, Optional[dict]]:
    """Return (path_params, query_params, json_body)."""
    path_params: Dict[str, Any] = {}
    query_params: Dict[str, Any] = {}
    json_body: Optional[dict] = None

    for p in op.get("parameters", []) or []:
        if "$ref" in p:
            p = _resolve_ref(spec, p["$ref"])
        name = p["name"]
        where = p.get("in")
        required = bool(p.get("required"))
        schema = p.get("schema", {})

        if where == "path":
            # Always required by OpenAPI convention, but keep consistent.
            path_params[name] = _schema_to_example(spec, schema) or "id"
        elif where == "query" and required:
            query_params[name] = _schema_to_example(spec, schema)

    if "requestBody" in op:
        rb = op["requestBody"]
        if "$ref" in rb:
            rb = _resolve_ref(spec, rb["$ref"])
        content = (rb.get("content") or {}).get("application/json")
        if content and "schema" in content:
            schema = content["schema"]
            json_body = _schema_to_example(spec, schema)

    return path_params, query_params, json_body


def _format_path(path: str, path_params: dict) -> str:
    for k, v in path_params.items():
        path = path.replace("{" + k + "}", str(v))
    return path


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _get_operation_method(video_sdk: Any, operation_id: str):
    candidates = [
        operation_id,
        _snake_case(operation_id),
    ]
    for name in candidates:
        if hasattr(video_sdk, name):
            return getattr(video_sdk, name), name
    raise AttributeError(
        f"video_sdk is missing an operation method for operationId '{operation_id}'. "
        f"Tried: {candidates}"
    )


def _validate_jsonschema(spec: dict, schema: dict, instance: Any) -> None:
    if jsonschema is None:
        pytest.skip("jsonschema is not installed; cannot validate OpenAPI response schemas")

    # Convert OpenAPI-ish schema to JSON Schema-ish validation by providing a resolver store.
    store = {
        "#/components/schemas": spec.get("components", {}).get("schemas", {}),
    }

    # jsonschema wants full ref targets, but our schemas use local refs. Easiest: feed
    # the entire spec as the root schema document and validate against a wrapper.
    wrapper = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "allOf": [schema],
    }

    resolver = jsonschema.RefResolver.from_schema(spec)  # type: ignore[attr-defined]
    jsonschema.Draft202012Validator(wrapper, resolver=resolver).validate(instance)  # type: ignore[attr-defined]


# -------------------------
# Client fixture expectation
# -------------------------

@pytest.fixture
def zoom_client():
    """Create a client instance.

    Contract expectation for the project:
      - package name: `zoompy`
      - client class: `ZoomClient`
      - constructor accepts `token` and optional `base_url`
      - exposes `.video_sdk` endpoint group

    If your implementation differs, update THIS fixture, not 38 tests.
    """
    from zoompy import ZoomClient  # type: ignore

    base_url = os.environ.get("ZOOM_BASE_URL", "https://api.zoom.us/v2")
    token = os.environ.get("ZOOM_TOKEN", "test-token")
    return ZoomClient(token=token, base_url=base_url)


# -------------------------
# Tests
# -------------------------


def test_video_sdk_group_exists(zoom_client):
    assert hasattr(zoom_client, "video_sdk"), "Client must expose a .video_sdk endpoint group"


@dataclass(frozen=True)
class OperationCase:
    operation_id: str
    http_method: str
    path: str
    success_code: str


def _operation_cases() -> list[OperationCase]:
    spec = _load_spec()
    cases: list[OperationCase] = []
    for operation_id, http_method, path, op in _iter_operations(spec):
        success = _pick_success_response(op)
        if not success:
            continue
        cases.append(OperationCase(operation_id, http_method, path, success))
    return cases


@pytest.mark.parametrize("case", _operation_cases(), ids=lambda c: f"{c.http_method.upper()} {c.path} ({c.operation_id})")
@pytest.mark.anyio
async def test_video_sdk_operations_send_correct_request_and_validate_success_response(case: OperationCase, zoom_client):
    """Schema-driven contract test across *all* operations in the Video SDK spec."""
    spec = _load_spec()

    # Locate the operation object again.
    op = spec["paths"][case.path][case.http_method]

    video_sdk = zoom_client.video_sdk
    method, resolved_name = _get_operation_method(video_sdk, case.operation_id)

    path_params, query_params, json_body = _build_request_kwargs(spec, case.path, op)
    url_path = _format_path(case.path, path_params)

    base_url = getattr(zoom_client, "base_url", os.environ.get("ZOOM_BASE_URL", "https://api.zoom.us/v2"))

    # Build a success response payload (if any) from the OpenAPI response schema.
    response_obj = op["responses"][case.success_code]
    if "$ref" in response_obj:
        response_obj = _resolve_ref(spec, response_obj["$ref"])

    response_json: Any = None
    if case.success_code != "204":
        content = (response_obj.get("content") or {}).get("application/json")
        if content and "schema" in content:
            response_schema = content["schema"]
            response_json = _schema_to_example(spec, response_schema)
        else:
            response_json = {}

    with respx.mock(assert_all_called=True, assert_all_mocked=True) as router:
        route = router.request(case.http_method, f"{base_url}{url_path}")
        if case.success_code == "204":
            route.respond(status_code=204)
        else:
            route.respond(status_code=int(case.success_code), json=response_json)

        # Call with kwargs matching spec parameter names.
        call_kwargs: Dict[str, Any] = {}
        call_kwargs.update(path_params)
        call_kwargs.update(query_params)
        if json_body is not None:
            # Most SDKs name this `data` or `body` or `json`. We accept a few.
            # Prefer the most explicit: `json`.
            sig = None
            try:
                sig = inspect.signature(method)
            except Exception:
                sig = None

            if sig and "json" in sig.parameters:
                call_kwargs["json"] = json_body
            elif sig and "body" in sig.parameters:
                call_kwargs["body"] = json_body
            elif sig and "data" in sig.parameters:
                call_kwargs["data"] = json_body
            else:
                # Fall back to passing nothing special and hope implementation uses **kwargs.
                call_kwargs["json"] = json_body

        result = await _maybe_await(method(**call_kwargs))

        assert route.called, (
            f"Expected {resolved_name}(...) to call {case.http_method.upper()} {url_path}, "
            "but no matching request was made."
        )

        if case.success_code == "204":
            assert result is None or result == {}, "204 operations should return None (or an empty dict)"
            return

        # Validate schema if possible.
        content = (response_obj.get("content") or {}).get("application/json")
        if content and "schema" in content:
            _validate_jsonschema(spec, content["schema"], result)
        else:
            assert isinstance(result, (dict, list)), "Expected JSON-like response"


def test_list_sessions_requires_from_and_to_before_http_call(zoom_client):
    """GET /videosdk/sessions requires from/to, and the client should enforce that."""
    spec = _load_spec()
    op = spec["paths"]["/videosdk/sessions"]["get"]

    video_sdk = zoom_client.video_sdk
    method, _ = _get_operation_method(video_sdk, op["operationId"])

    base_url = getattr(zoom_client, "base_url", os.environ.get("ZOOM_BASE_URL", "https://api.zoom.us/v2"))

    with respx.mock(assert_all_called=False, assert_all_mocked=False) as router:
        route = router.get(f"{base_url}/videosdk/sessions").respond(200, json={"sessions": []})

        # Missing both required fields.
        with pytest.raises((TypeError, ValueError)):
            method()  # type: ignore[misc]
        assert not route.called, "Client should fail fast before making an HTTP request"

        # Missing one of the two.
        with pytest.raises((TypeError, ValueError)):
            method(from="2021-12-01")  # type: ignore[misc]
        assert not route.called

        with pytest.raises((TypeError, ValueError)):
            method(to="2021-12-02")  # type: ignore[misc]
        assert not route.called


@pytest.mark.anyio
async def test_create_session_requires_session_name_before_http_call(zoom_client):
    """POST /videosdk/sessions requires session_name, and the client should enforce that."""
    spec = _load_spec()
    op = spec["paths"]["/videosdk/sessions"]["post"]

    video_sdk = zoom_client.video_sdk
    method, _ = _get_operation_method(video_sdk, op["operationId"])

    base_url = getattr(zoom_client, "base_url", os.environ.get("ZOOM_BASE_URL", "https://api.zoom.us/v2"))

    with respx.mock(assert_all_called=False, assert_all_mocked=False) as router:
        route = router.post(f"{base_url}/videosdk/sessions").respond(201, json={"session_id": "abc"})

        # Try to call with an empty body.
        with pytest.raises((TypeError, ValueError)):
            await _maybe_await(method(json={}))
        assert not route.called, "Client should fail fast before making an HTTP request"

        # Still missing session_name.
        with pytest.raises((TypeError, ValueError)):
            await _maybe_await(method(json={"settings": {"auto_recording": "none"}}))
        assert not route.called


@pytest.mark.anyio
async def test_path_params_are_required_before_http_call(zoom_client):
    """A representative path-param operation should require its path param."""
    spec = _load_spec()
    # /videosdk/sessions/{sessionId} is a clean example.
    op = spec["paths"]["/videosdk/sessions/{sessionId}"]["get"]

    video_sdk = zoom_client.video_sdk
    method, _ = _get_operation_method(video_sdk, op["operationId"])

    base_url = getattr(zoom_client, "base_url", os.environ.get("ZOOM_BASE_URL", "https://api.zoom.us/v2"))

    with respx.mock(assert_all_called=False, assert_all_mocked=False) as router:
        # This route should never be hit because we're omitting sessionId.
        route = router.get(f"{base_url}/videosdk/sessions/test-session").respond(200, json={})

        with pytest.raises((TypeError, ValueError)):
            await _maybe_await(method())  # type: ignore[misc]

        assert not route.called, "Client should fail fast before making an HTTP request"


def test_schema_file_is_valid_openapi_document():
    spec = _load_spec()
    assert spec.get("openapi", "").startswith("3."), "Expected an OpenAPI 3.x document"
    assert "paths" in spec and isinstance(spec["paths"], dict) and spec["paths"], "OpenAPI document must define paths"