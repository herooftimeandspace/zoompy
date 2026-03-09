"""Contract tests for the Zoom Auto Dialer API endpoints.

Schema location (repo layout):
    src/tests/schemas/business_services/Auto Dialer.json

This suite is schema-driven and endpoint-contract driven:
- It loads the OpenAPI document.
- It asserts the client exposes an Auto Dialer service group.
- It validates request construction (method + path + required params).
- It validates success responses against the documented JSON schema (when possible).
- It enforces a few critical behavioral rules called out in endpoint descriptions.

If your implementation names differ, adjust the `zoom_client` fixture and `_svc(...)` helper.
"""

from __future__ import annotations

import inspect
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

import pytest
import respx

try:
    import jsonschema
except Exception:  # pragma: no cover
    jsonschema = None  # type: ignore


SCHEMA_PATH = Path("src/tests/schemas/business_services/Auto Dialer.json")


# -------------------------
# Helpers: OpenAPI handling
# -------------------------


def _load_spec() -> dict:
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(
            f"Missing OpenAPI schema at {SCHEMA_PATH}. "
            "Make sure you copied the provided 'Auto Dialer.json' into that path."
        )
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _snake_case(name: str) -> str:
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return re.sub(r"__+", "_", s).strip("_").lower()


def _iter_operations(spec: dict) -> Iterable[Tuple[str, str, str, dict]]:
    for path, item in spec.get("paths", {}).items():
        for method, op in item.items():
            if method.startswith("x-"):
                continue
            operation_id = op.get("operationId")
            if not operation_id:
                continue
            yield operation_id, method.lower(), path, op


def _pick_success_response(op: dict) -> Optional[str]:
    codes = [str(c) for c in op.get("responses", {}).keys()]
    success = [c for c in codes if c.startswith("2")]
    if not success:
        return None
    if "200" in success:
        return "200"
    if "201" in success:
        return "201"
    if "204" in success:
        return "204"
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
    """Generate a minimal example that is likely to satisfy the schema."""
    if "$ref" in schema:
        return _schema_to_example(spec, _resolve_ref(spec, schema["$ref"]))

    if "example" in schema:
        return schema["example"]
    if "default" in schema:
        return schema["default"]
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]

    t = schema.get("type")

    if t == "object" or (t is None and "properties" in schema):
        props: dict = schema.get("properties", {})
        required = list(schema.get("required", []))
        out: Dict[str, Any] = {}
        for k in required:
            out[k] = _schema_to_example(spec, props.get(k, {}))
        if not out and props:
            k = next(iter(props.keys()))
            out[k] = _schema_to_example(spec, props[k])
        return out

    if t == "array":
        items = schema.get("items", {})
        min_items = int(schema.get("minItems", 0) or 0)
        count = max(1, min_items)
        return [_schema_to_example(spec, items) for _ in range(count)]

    if t == "string":
        fmt = schema.get("format")
        if fmt == "date-time":
            return "2025-01-01T00:00:00Z"
        if fmt == "date":
            return "2025-01-01"
        return "string"

    if t == "integer":
        return int(schema.get("example", 1))

    if t == "number":
        ex = schema.get("example", 1)
        try:
            return float(ex)
        except Exception:
            return 1.0

    if t == "boolean":
        return True

    return schema.get("example", {})


def _build_request_kwargs(spec: dict, path: str, op: dict) -> Tuple[dict, dict, Optional[dict]]:
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
            path_params[name] = _schema_to_example(spec, schema) or "id"
        elif where == "query" and required:
            query_params[name] = _schema_to_example(spec, schema)

    if "requestBody" in op:
        rb = op["requestBody"]
        if "$ref" in rb:
            rb = _resolve_ref(spec, rb["$ref"])
        content = (rb.get("content") or {}).get("application/json")
        if content and "schema" in content:
            json_body = _schema_to_example(spec, content["schema"])

    return path_params, query_params, json_body


def _format_path(path: str, path_params: dict) -> str:
    for k, v in path_params.items():
        path = path.replace("{" + k + "}", str(v))
    return path


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _get_operation_method(service: Any, operation_id: str):
    candidates = [operation_id, _snake_case(operation_id)]
    for name in candidates:
        if hasattr(service, name):
            return getattr(service, name), name
    raise AttributeError(
        f"Auto Dialer service missing method for operationId '{operation_id}'. Tried: {candidates}"
    )


def _validate_jsonschema(spec: dict, schema: dict, instance: Any) -> None:
    if jsonschema is None:
        pytest.skip("jsonschema is not installed; cannot validate OpenAPI response schemas")

    resolver = jsonschema.RefResolver.from_schema(spec)  # type: ignore[attr-defined]
    wrapper = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "allOf": [schema],
    }
    jsonschema.Draft202012Validator(wrapper, resolver=resolver).validate(instance)  # type: ignore[attr-defined]


# -------------------------
# Client fixture expectation
# -------------------------


@pytest.fixture
def zoom_client():
    """Create a client instance.

    Contract expectation for this project:
      - package: `zoompy`
      - client: `ZoomClient`
      - constructor accepts `token` and optional `base_url`

    If your implementation differs, update this fixture.
    """

    from zoompy import ZoomClient  # type: ignore

    base_url = os.environ.get("ZOOM_BASE_URL", "https://api.zoom.us/v2")
    token = os.environ.get("ZOOM_TOKEN", "test-token")
    return ZoomClient(token=token, base_url=base_url)


def _svc(client: Any) -> Any:
    """Locate the auto dialer service group.

    Preferred: `client.auto_dialer`
    Fallbacks: `client.dialer`, `client.autoDialer`
    """

    if hasattr(client, "auto_dialer"):
        return client.auto_dialer
    if hasattr(client, "dialer"):
        return client.dialer
    if hasattr(client, "autoDialer"):
        return client.autoDialer
    raise AssertionError("Client must expose Auto Dialer service at .auto_dialer (or .dialer)")


# -------------------------
# Generic schema-driven tests
# -------------------------


def test_auto_dialer_group_exists(zoom_client):
    _ = _svc(zoom_client)


def _operation_cases() -> list[Tuple[str, str, str, str]]:
    spec = _load_spec()
    out: list[Tuple[str, str, str, str]] = []
    for operation_id, method, path, op in _iter_operations(spec):
        success = _pick_success_response(op)
        if not success:
            continue
        out.append((operation_id, method, path, success))
    return out


@pytest.mark.parametrize(
    "operation_id, http_method, path, success_code",
    _operation_cases(),
    ids=lambda c: f"{c[1].upper()} {c[2]} ({c[0]})",
)
@pytest.mark.anyio
async def test_auto_dialer_operations_send_correct_request_and_validate_success_response(
    operation_id: str,
    http_method: str,
    path: str,
    success_code: str,
    zoom_client,
):
    """Covers every operation in the Auto Dialer OpenAPI spec."""

    spec = _load_spec()
    op = spec["paths"][path][http_method]

    svc = _svc(zoom_client)
    method, resolved_name = _get_operation_method(svc, operation_id)

    path_params, query_params, json_body = _build_request_kwargs(spec, path, op)
    url_path = _format_path(path, path_params)

    base_url = getattr(zoom_client, "base_url", os.environ.get("ZOOM_BASE_URL", "https://api.zoom.us/v2"))

    # Success response payload from the response schema, if present.
    response_obj = op["responses"][success_code]
    if "$ref" in response_obj:
        response_obj = _resolve_ref(spec, response_obj["$ref"])

    response_json: Any = None
    if success_code != "204":
        content = (response_obj.get("content") or {}).get("application/json")
        if content and "schema" in content:
            response_json = _schema_to_example(spec, content["schema"])
        else:
            response_json = {}

    with respx.mock(assert_all_called=True, assert_all_mocked=True) as router:
        route = router.request(http_method, f"{base_url}{url_path}")
        if success_code == "204":
            route.respond(status_code=204)
        else:
            route.respond(status_code=int(success_code), json=response_json)

        # Build kwargs. We pass required query params and path params. For bodies, we try `json`/`body`/`data`.
        call_kwargs: Dict[str, Any] = {}
        call_kwargs.update(path_params)
        call_kwargs.update(query_params)

        if json_body is not None:
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
                call_kwargs["json"] = json_body

        result = await _maybe_await(method(**call_kwargs))

        assert route.called, (
            f"Expected {resolved_name}(...) to call {http_method.upper()} {url_path}, "
            "but no matching request was made."
        )

        if success_code == "204":
            assert result is None or result == {}, "204 operations should return None (or an empty dict)"
            return

        content = (response_obj.get("content") or {}).get("application/json")
        if content and "schema" in content:
            _validate_jsonschema(spec, content["schema"], result)
        else:
            assert isinstance(result, (dict, list)), "Expected JSON-like response"


# -------------------------
# Targeted behavioral rules
# -------------------------


def test_get_call_history_requires_at_least_one_filter_before_http_call(zoom_client):
    """GET /dialer/call-history: spec says at least one filter must be provided."""

    spec = _load_spec()
    op = spec["paths"]["/dialer/call-history"]["get"]

    svc = _svc(zoom_client)
    fn, _ = _get_operation_method(svc, op["operationId"])

    base_url = getattr(zoom_client, "base_url", os.environ.get("ZOOM_BASE_URL", "https://api.zoom.us/v2"))

    with respx.mock(assert_all_called=False, assert_all_mocked=False) as router:
        route = router.get(f"{base_url}/dialer/call-history").respond(200, json={"total": 0, "call_history": []})

        with pytest.raises((TypeError, ValueError)):
            fn()  # type: ignore[misc]

        assert not route.called, "Client should fail fast before making an HTTP request"


@pytest.mark.parametrize(
    "params",
    [
        {"call_id": "068b9f5120201001631", "user_id": "user"},
        {"call_id": "068b9f5120201001631", "call_list_id": "list"},
        {"call_id": "068b9f5120201001631", "keyword": "John"},
        {"call_list_id": "list", "user_id": "user"},
        {"import_from": "OPEN_API", "call_list_id": "list"},
        {"import_from": "OPEN_API", "call_id": "068b9f5120201001631"},
    ],
    ids=lambda p: ",".join(sorted(p.keys())),
)
def test_get_call_history_rejects_invalid_filter_combinations(params: dict, zoom_client):
    """GET /dialer/call-history: description documents mutually-exclusive filters."""

    spec = _load_spec()
    op = spec["paths"]["/dialer/call-history"]["get"]

    svc = _svc(zoom_client)
    fn, _ = _get_operation_method(svc, op["operationId"])

    base_url = getattr(zoom_client, "base_url", os.environ.get("ZOOM_BASE_URL", "https://api.zoom.us/v2"))

    with respx.mock(assert_all_called=False, assert_all_mocked=False) as router:
        route = router.get(f"{base_url}/dialer/call-history").respond(200, json={"total": 0, "call_history": []})

        with pytest.raises(ValueError):
            fn(**params)  # type: ignore[misc]

        assert not route.called, "Client should fail fast before making an HTTP request"


@pytest.mark.anyio
async def test_create_call_list_requires_required_fields_before_http_call(zoom_client):
    """POST /dialer/call-lists: requestBody schema requires name, prospect_type, assigned_to_user_id."""

    spec = _load_spec()
    op = spec["paths"]["/dialer/call-lists"]["post"]

    svc = _svc(zoom_client)
    fn, _ = _get_operation_method(svc, op["operationId"])

    base_url = getattr(zoom_client, "base_url", os.environ.get("ZOOM_BASE_URL", "https://api.zoom.us/v2"))

    with respx.mock(assert_all_called=False, assert_all_mocked=False) as router:
        route = router.post(f"{base_url}/dialer/call-lists").respond(201, json={"call_list_id": "id"})

        with pytest.raises((TypeError, ValueError)):
            await _maybe_await(fn(json={}))
        assert not route.called

        with pytest.raises((TypeError, ValueError)):
            await _maybe_await(fn(json={"name": "n"}))
        assert not route.called

        with pytest.raises((TypeError, ValueError)):
            await _maybe_await(fn(json={"name": "n", "prospect_type": "CONTACT"}))
        assert not route.called


@pytest.mark.anyio
async def test_update_call_list_requires_at_least_one_field_before_http_call(zoom_client):
    """PATCH /dialer/call-lists/{callListId}: description says at least one field must be provided."""

    spec = _load_spec()
    op = spec["paths"]["/dialer/call-lists/{callListId}"]["patch"]

    svc = _svc(zoom_client)
    fn, _ = _get_operation_method(svc, op["operationId"])

    base_url = getattr(zoom_client, "base_url", os.environ.get("ZOOM_BASE_URL", "https://api.zoom.us/v2"))
    call_list_id = "abc123xyz"

    with respx.mock(assert_all_called=False, assert_all_mocked=False) as router:
        route = router.patch(f"{base_url}/dialer/call-lists/{call_list_id}").respond(204)

        with pytest.raises((TypeError, ValueError)):
            await _maybe_await(fn(callListId=call_list_id, json={}))  # type: ignore[misc]

        assert not route.called


@pytest.mark.anyio
async def test_create_prospect_requires_name_and_phone_before_http_call(zoom_client):
    """POST /dialer/call-lists/{callListId}/prospects: description requires name + at least one phone."""

    spec = _load_spec()
    op = spec["paths"]["/dialer/call-lists/{callListId}/prospects"]["post"]

    svc = _svc(zoom_client)
    fn, _ = _get_operation_method(svc, op["operationId"])

    base_url = getattr(zoom_client, "base_url", os.environ.get("ZOOM_BASE_URL", "https://api.zoom.us/v2"))
    call_list_id = "c-ncE_MXQACAjn3_I_35gg"

    with respx.mock(assert_all_called=False, assert_all_mocked=False) as router:
        route = router.post(f"{base_url}/dialer/call-lists/{call_list_id}/prospects").respond(
            201, json={"prospect_id": "p"}
        )

        # Missing everything.
        with pytest.raises((TypeError, ValueError)):
            await _maybe_await(fn(callListId=call_list_id, json={}))  # type: ignore[misc]
        assert not route.called

        # Has a phone but no name.
        with pytest.raises(ValueError):
            await _maybe_await(
                fn(
                    callListId=call_list_id,
                    json={"phone_numbers": [{"number": "1-555-123-4567"}]},
                )
            )
        assert not route.called

        # Has a name but no phone.
        with pytest.raises(ValueError):
            await _maybe_await(fn(callListId=call_list_id, json={"primary_name": "John"}))
        assert not route.called


def test_schema_file_is_valid_openapi_document():
    spec = _load_spec()
    assert str(spec.get("openapi", "")).startswith("3."), "Expected an OpenAPI 3.x document"
    assert "paths" in spec and isinstance(spec["paths"], dict) and spec["paths"], "OpenAPI must define paths"

    # Quick sanity: key endpoints exist.
    assert "/dialer/call-history" in spec["paths"]
    assert "/dialer/call-lists" in spec["paths"]
