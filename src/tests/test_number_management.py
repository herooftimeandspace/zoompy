"""Contract tests for the Zoom Number Management API endpoint group.

Schema source:
  src/tests/schemas/build_platform/Number Management.json

Library policy for this repo's test suites:
  - httpx, pytest, respx, jsonschema (Draft202012Validator)

These tests validate *client implementations* (not live servers):
  - Load the OpenAPI spec.
  - Ensure the client exposes `.number_management`.
  - Ensure each OpenAPI operation maps to a callable.
  - Ensure correct HTTP method + path are used.
  - Validate returned JSON against the response schema.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import httpx
import pytest
import respx
from jsonschema import Draft202012Validator


SCHEMA_PATH = Path("src/tests/schemas/build_platform/Number Management.json")


# -------------------------
# OpenAPI helpers
# -------------------------


def _load_spec() -> dict:
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"Missing OpenAPI schema at {SCHEMA_PATH}")
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _snake_case(name: str) -> str:
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return re.sub(r"__+", "_", s).strip("_").lower()


def _iter_operations(spec: Mapping[str, Any]) -> Iterable[tuple[str, str, str, dict]]:
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            m = str(method).lower()
            if m not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not isinstance(op, dict):
                continue
            op_id = op.get("operationId")
            if not op_id:
                continue
            yield str(op_id), m, str(path), op


def _pick_success_response(op: Mapping[str, Any]) -> str | None:
    codes = [str(c) for c in (op.get("responses") or {}).keys()]
    success = [c for c in codes if c.startswith("2")]
    if not success:
        return None
    if "200" in success:
        return "200"
    for preferred in ("201", "202", "204"):
        if preferred in success:
            return preferred
    return sorted(success)[0]


def _resolve_ref(spec: Mapping[str, Any], ref: str) -> dict:
    if not ref.startswith("#/"):
        raise ValueError(f"Only local $ref is supported in tests: {ref}")
    node: Any = spec
    for part in ref.lstrip("#/").split("/"):
        node = node[part]
    if not isinstance(node, dict):
        raise TypeError(f"Resolved $ref is not an object: {ref}")
    return node


def _deep_deref(spec: Mapping[str, Any], schema: Any) -> Any:
    if isinstance(schema, dict):
        if "$ref" in schema and isinstance(schema["$ref"], str):
            return _deep_deref(spec, _resolve_ref(spec, schema["$ref"]))
        return {k: _deep_deref(spec, v) for k, v in schema.items() if k != "$ref"}
    if isinstance(schema, list):
        return [_deep_deref(spec, x) for x in schema]
    return schema


def _schema_to_example(spec: Mapping[str, Any], schema: Any) -> Any:
    schema = _deep_deref(spec, schema)
    if not isinstance(schema, dict):
        return None

    if "example" in schema:
        return schema["example"]
    if "default" in schema:
        return schema["default"]
    if "enum" in schema and isinstance(schema["enum"], list) and schema["enum"]:
        return schema["enum"][0]

    if "oneOf" in schema and isinstance(schema["oneOf"], list) and schema["oneOf"]:
        return _schema_to_example(spec, schema["oneOf"][0])
    if "anyOf" in schema and isinstance(schema["anyOf"], list) and schema["anyOf"]:
        return _schema_to_example(spec, schema["anyOf"][0])
    if "allOf" in schema and isinstance(schema["allOf"], list) and schema["allOf"]:
        merged: dict[str, Any] = {}
        for s in schema["allOf"]:
            v = _schema_to_example(spec, s)
            if isinstance(v, dict):
                merged.update(v)
        return merged or _schema_to_example(spec, schema["allOf"][0])

    t = schema.get("type")

    if t == "object" or (t is None and "properties" in schema):
        props = schema.get("properties") or {}
        req = schema.get("required") or []
        out: dict[str, Any] = {}
        if isinstance(req, list):
            for name in req:
                if name in props:
                    out[name] = _schema_to_example(spec, props[name])
        if not out and isinstance(props, dict) and props:
            k = next(iter(props.keys()))
            out[k] = _schema_to_example(spec, props[k])
        return out

    if t == "array":
        items = schema.get("items") or {}
        return [_schema_to_example(spec, items)]

    if t == "string":
        fmt = schema.get("format")
        if fmt == "date":
            return "2025-01-01"
        if fmt == "date-time":
            return "2025-01-01T00:00:00Z"
        return "string"

    if t == "integer":
        return 1

    if t == "number":
        return 1.0

    if t == "boolean":
        return True

    return {}


def _build_required_kwargs(spec: Mapping[str, Any], op: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    kwargs: dict[str, Any] = {}

    for p in op.get("parameters") or []:
        if not isinstance(p, dict):
            continue
        p = _deep_deref(spec, p)
        where = p.get("in")
        name = p.get("name")
        required = bool(p.get("required"))
        schema = p.get("schema") or {}

        if not required or not name:
            continue
        if where in {"path", "query"}:
            kwargs[str(name)] = _schema_to_example(spec, schema)

    json_body = None
    if "requestBody" in op and isinstance(op["requestBody"], dict):
        rb = _deep_deref(spec, op["requestBody"])
        if rb.get("required"):
            content = (rb.get("content") or {}).get("application/json")
            if isinstance(content, dict) and "schema" in content:
                json_body = _schema_to_example(spec, content["schema"])

    return kwargs, json_body


def _format_path(path_tmpl: str, kwargs: Mapping[str, Any]) -> str:
    out = path_tmpl
    for key, val in kwargs.items():
        out = out.replace("{" + str(key) + "}", str(val))
    return out


def _response_schema(spec: Mapping[str, Any], *, path: str, method: str, status: str) -> dict | None:
    op = spec["paths"][path][method]
    resp = (op.get("responses") or {}).get(status)
    if not isinstance(resp, dict):
        return None
    resp = _deep_deref(spec, resp)
    content = (resp.get("content") or {}).get("application/json")
    if isinstance(content, dict) and "schema" in content:
        schema = _deep_deref(spec, content["schema"])
        if isinstance(schema, dict):
            return schema
    return None


def _validate(instance: Any, schema: dict) -> None:
    Draft202012Validator(schema).validate(instance)


# -------------------------
# Client hook
# -------------------------


@pytest.fixture
def zoom_client():
    from zoompy import ZoomClient  # type: ignore

    return ZoomClient(token="test-token", base_url="https://api.zoom.us/v2")


def _svc(client: Any) -> Any:
    if hasattr(client, "number_management"):
        return client.number_management
    if hasattr(client, "numbers"):
        return client.numbers
    raise AssertionError("Client must expose Number Management service at .number_management (or .numbers)")


def _get_method(service: Any, operation_id: str):
    for name in (operation_id, _snake_case(operation_id)):
        if hasattr(service, name):
            fn = getattr(service, name)
            if callable(fn):
                return fn, name
    raise AttributeError(
        f"Number Management service missing method for operationId '{operation_id}'. "
        f"Tried: {operation_id}, {_snake_case(operation_id)}"
    )


# -------------------------
# Tests
# -------------------------


def test_number_management_group_exists(zoom_client: Any) -> None:
    _ = _svc(zoom_client)


@dataclass(frozen=True)
class OperationCase:
    operation_id: str
    method: str
    path: str
    success_status: str


def _cases() -> list[OperationCase]:
    spec = _load_spec()
    out: list[OperationCase] = []
    for operation_id, method, path, op in _iter_operations(spec):
        success = _pick_success_response(op)
        if success:
            out.append(OperationCase(operation_id, method, path, success))
    return out


@pytest.mark.parametrize("case", _cases(), ids=lambda c: f"{c.method.upper()} {c.path} ({c.operation_id})")
def test_number_management_operations_are_callable(zoom_client: Any, case: OperationCase) -> None:
    svc = _svc(zoom_client)
    _get_method(svc, case.operation_id)


@pytest.mark.parametrize("case", _cases(), ids=lambda c: f"{c.method.upper()} {c.path} ({c.operation_id})")
def test_number_management_operation_makes_expected_http_call_and_validates_response(
    zoom_client: Any, case: OperationCase
) -> None:
    spec = _load_spec()
    op = spec["paths"][case.path][case.method]

    svc = _svc(zoom_client)
    fn, resolved_name = _get_method(svc, case.operation_id)

    kwargs, json_body = _build_required_kwargs(spec, op)
    url_path = _format_path(case.path, kwargs)

    schema = _response_schema(spec, path=case.path, method=case.method, status=case.success_status)
    response_json = None
    if case.success_status != "204" and schema is not None:
        response_json = _schema_to_example(spec, schema)
    elif case.success_status != "204":
        response_json = {}

    base_url = getattr(zoom_client, "base_url", "https://api.zoom.us/v2")

    with respx.mock(assert_all_called=True, assert_all_mocked=True):
        route = respx.request(case.method, f"{base_url}{url_path}")
        if case.success_status == "204":
            route.respond(204)
        else:
            route.respond(int(case.success_status), json=response_json)

        call_kwargs = dict(kwargs)
        if json_body is not None:
            call_kwargs["json"] = json_body

        result = fn(**call_kwargs)

        assert route.called, (
            f"Expected {resolved_name}(...) to call {case.method.upper()} {url_path} "
            "but no matching request was made."
        )

        if case.success_status == "204":
            assert result is None or result == {}, "204 operations should return None (or empty dict)"
            return

        if schema is not None:
            _validate(result, _deep_deref(spec, schema))
        else:
            assert isinstance(result, (dict, list)), "Expected JSON-like response"


def test_number_management_missing_required_path_param_fails_fast(zoom_client: Any) -> None:
    spec = _load_spec()

    target = None
    for operation_id, method, path, op in _iter_operations(spec):
        params = op.get("parameters") or []
        if any(isinstance(p, dict) and p.get("in") == "path" and p.get("required") for p in params):
            success = _pick_success_response(op)
            if success:
                target = OperationCase(operation_id, method, path, success)
                break

    if target is None:
        pytest.skip("No operations with required path params found in Number Management schema")

    svc = _svc(zoom_client)
    fn, _ = _get_method(svc, target.operation_id)

    with respx.mock(assert_all_called=False, assert_all_mocked=False):
        with pytest.raises((TypeError, ValueError)):
            fn()  # type: ignore[misc]

        assert len(respx.calls) == 0, "Client should fail fast before making an HTTP request"


def test_number_management_schema_file_sanity() -> None:
    spec = _load_spec()
    assert str(spec.get("openapi", "")).startswith("3."), "Expected OpenAPI 3.x"
    assert "paths" in spec and isinstance(spec["paths"], dict) and spec["paths"], "OpenAPI must define paths"
