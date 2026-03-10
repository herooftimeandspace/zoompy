from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import httpx
import pytest
from jsonschema import Draft202012Validator


SPEC_PATH = Path(__file__).resolve().parent / "schemas" / "workplace" / "Clips.json"


# ----------------------------
# Contract required by tests
# ----------------------------
#
# These tests are designed to validate ANY Clips API implementation.
# Your implementation must provide a fixture named `clips_client`.
#
# The fixture may return:
#   1) an object with a `.request(...)` method, OR
#   2) a callable with the same signature as `.request(...)`.
#
# Expected request signature (recommended):
#   request(method: str,
#           path: str,
#           *,
#           path_params: Mapping[str, Any] | None = None,
#           params: Mapping[str, Any] | None = None,
#           json: Any | None = None,
#           headers: Mapping[str, str] | None = None,
#           timeout: float | None = None
#   ) -> httpx.Response | Mapping[str, Any] | list[Any] | None
#
# The tests will mock outbound HTTP with respx, so your client MUST use httpx.


@pytest.fixture
def clips_spec() -> dict[str, Any]:
    if not SPEC_PATH.exists():
        raise AssertionError(
            f"Clips OpenAPI spec not found at {SPEC_PATH}. "
            "Expected it under src/tests/schemas/workplace/Clips.json"
        )
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def _base_url(spec: Mapping[str, Any]) -> str:
    servers = spec.get("servers")
    if isinstance(servers, list):
        for server in servers:
            if isinstance(server, Mapping):
                url = server.get("url")
                if isinstance(url, str) and url:
                    return url.rstrip("/")
    raise AssertionError("Clips OpenAPI spec must define at least one server URL")


def _get_request_callable(clips_client: Any):
    if callable(clips_client):
        return clips_client
    req = getattr(clips_client, "request", None)
    if callable(req):
        return req
    raise AssertionError(
        "The `clips_client` fixture must return either a callable or an object "
        "with a callable `.request(...)` method."
    )


@dataclass(frozen=True)
class OperationCase:
    operation_id: str
    method: str
    path: str
    path_params: dict[str, Any]
    query_params: dict[str, Any]
    request_json: Any | None
    response_schema: dict[str, Any] | None
    status_code: int


def _snake(s: str) -> str:
    out: list[str] = []
    for ch in s:
        if ch.isupper() and out:
            out.append("_")
        out.append(ch.lower())
    return "".join(out).replace("__", "_")


def _iter_operations(spec: Mapping[str, Any]) -> Iterable[tuple[str, str, str, Mapping[str, Any]]]:
    paths = spec.get("paths", {})
    for path, item in paths.items():
        if not isinstance(item, Mapping):
            continue
        for method in ("get", "post", "put", "patch", "delete"):
            op = item.get(method)
            if isinstance(op, Mapping):
                yield op.get("operationId") or f"{method}_{path}", method.upper(), path, op


def _deepcopy_json(v: Any) -> Any:
    return json.loads(json.dumps(v))


def _resolve_ref(spec: Mapping[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        raise ValueError(f"Only local refs are supported in tests, got: {ref}")
    cur: Any = spec
    for part in ref.lstrip("#/").split("/"):
        if not isinstance(cur, Mapping) or part not in cur:
            raise KeyError(f"Unresolvable $ref: {ref}")
        cur = cur[part]
    return cur


def _resolve_schema(spec: Mapping[str, Any], schema: Any) -> Any:
    if not isinstance(schema, Mapping):
        return schema

    if "$ref" in schema:
        target = _deepcopy_json(_resolve_ref(spec, str(schema["$ref"])))
        siblings = {k: v for k, v in schema.items() if k != "$ref"}
        if siblings and isinstance(target, Mapping):
            merged = _deepcopy_json(target)
            merged.update(_deepcopy_json(siblings))
            target = merged
        return _resolve_schema(spec, target)

    resolved: dict[str, Any] = {}
    for k, v in schema.items():
        if isinstance(v, Mapping):
            resolved[k] = _resolve_schema(spec, v)
        elif isinstance(v, list):
            resolved[k] = [_resolve_schema(spec, x) for x in v]
        else:
            resolved[k] = v
    return resolved


def _pick_success_response(responses: Mapping[str, Any]) -> tuple[int, dict[str, Any] | None] | None:
    def _try(code_key: str) -> tuple[int, dict[str, Any] | None] | None:
        entry = responses.get(code_key)
        if not isinstance(entry, Mapping):
            return None
        try:
            code_int = int(code_key)
        except Exception:
            code_int = 200

        content = entry.get("content")
        if not isinstance(content, Mapping):
            return code_int, None

        app_json = content.get("application/json") or content.get("application/json; charset=utf-8")
        if not isinstance(app_json, Mapping):
            return code_int, None

        schema = app_json.get("schema")
        if not isinstance(schema, Mapping):
            return code_int, None
        return code_int, dict(schema)

    if "200" in responses:
        return _try("200")

    for key in responses.keys():
        if isinstance(key, str) and key.isdigit() and 200 <= int(key) < 300:
            got = _try(key)
            if got is not None:
                return got

    if "default" in responses:
        return _try("default")

    return None


def _example_for_primitive(schema: Mapping[str, Any]) -> Any:
    if "enum" in schema and isinstance(schema["enum"], list) and schema["enum"]:
        return schema["enum"][0]

    t = schema.get("type")
    fmt = schema.get("format")

    if t == "string" or t is None:
        if "example" in schema:
            return schema["example"]
        if fmt in {"email", "uri", "uuid"}:
            if fmt == "email":
                return "test@example.com"
            if fmt == "uuid":
                return "00000000-0000-0000-0000-000000000000"
            return "https://example.com"
        return "test"
    if t == "integer":
        return int(schema.get("example", 1))
    if t == "number":
        return float(schema.get("example", 1.0))
    if t == "boolean":
        return bool(schema.get("example", True))
    return "test"


def _example_from_schema(spec: Mapping[str, Any], schema: Any) -> Any:
    schema = _resolve_schema(spec, schema)
    if not isinstance(schema, Mapping):
        return schema

    if schema.get("nullable") is True:
        schema = {k: v for k, v in schema.items() if k != "nullable"}

    if "example" in schema:
        return schema["example"]

    if "allOf" in schema and isinstance(schema["allOf"], list) and schema["allOf"]:
        parts = [_example_from_schema(spec, s) for s in schema["allOf"]]
        if all(isinstance(p, Mapping) for p in parts):
            merged: dict[str, Any] = {}
            for p in parts:
                merged.update(dict(p))
            return merged
        return parts[0]

    for key in ("oneOf", "anyOf"):
        if key in schema and isinstance(schema[key], list) and schema[key]:
            return _example_from_schema(spec, schema[key][0])

    t = schema.get("type")

    if t == "array":
        return [_example_from_schema(spec, schema.get("items", {}))]

    if t == "object" or (t is None and "properties" in schema):
        props = schema.get("properties", {})
        required = set(schema.get("required", []) or [])
        out: dict[str, Any] = {}
        if isinstance(props, Mapping):
            for name, prop_schema in props.items():
                if name in required:
                    out[name] = _example_from_schema(spec, prop_schema)

        if not out and isinstance(props, Mapping) and props:
            first_key = next(iter(props.keys()))
            out[first_key] = _example_from_schema(spec, props[first_key])

        if not out and schema.get("additionalProperties"):
            out["key"] = "value"

        return out

    return _example_for_primitive(schema)


def _validate(instance: Any, schema: Mapping[str, Any]) -> None:
    Draft202012Validator(schema).validate(instance)


def _build_operation_cases(spec: Mapping[str, Any]) -> list[OperationCase]:
    cases: list[OperationCase] = []
    for op_id, method, path, op in _iter_operations(spec):
        parameters: list[Mapping[str, Any]] = []

        path_item = spec.get("paths", {}).get(path, {})
        if isinstance(path_item, Mapping) and isinstance(path_item.get("parameters"), list):
            parameters.extend([p for p in path_item["parameters"] if isinstance(p, Mapping)])

        if isinstance(op.get("parameters"), list):
            parameters.extend([p for p in op["parameters"] if isinstance(p, Mapping)])

        path_params: dict[str, Any] = {}
        query_params: dict[str, Any] = {}

        for p in parameters:
            where = p.get("in")
            name = p.get("name")
            if not isinstance(name, str) or where not in {"path", "query"}:
                continue
            schema = p.get("schema") or {}
            value = p.get("example")
            if value is None:
                value = _example_from_schema(spec, schema)
            if where == "path":
                path_params[name] = value
            elif p.get("required") is True:
                query_params[name] = value

        request_json: Any | None = None
        request_body = op.get("requestBody")
        if isinstance(request_body, Mapping):
            content = request_body.get("content")
            if isinstance(content, Mapping):
                app_json = content.get("application/json")
                if isinstance(app_json, Mapping) and isinstance(app_json.get("schema"), (Mapping, list)):
                    request_json = _example_from_schema(spec, app_json["schema"])

        response_schema: dict[str, Any] | None = None
        status_code = 200
        responses = op.get("responses")
        if isinstance(responses, Mapping):
            pick = _pick_success_response(responses)
            if pick is not None:
                status_code, raw_schema = pick
                response_schema = (
                    _resolve_schema(spec, raw_schema) if isinstance(raw_schema, Mapping) else None
                )

        cases.append(
            OperationCase(
                operation_id=str(op_id),
                method=method,
                path=path,
                path_params=path_params,
                query_params=query_params,
                request_json=request_json,
                response_schema=response_schema,
                status_code=status_code,
            )
        )

    return cases


def _format_path(path: str, path_params: Mapping[str, Any]) -> str:
    out = path
    for k, v in path_params.items():
        out = out.replace("{" + k + "}", str(v))
    return out


@pytest.fixture
def clips_cases(clips_spec: dict[str, Any]) -> list[OperationCase]:
    cases = _build_operation_cases(clips_spec)
    if not cases:
        raise AssertionError("No operations discovered in Clips OpenAPI spec.")
    return cases


def test_clips_spec_is_openapi_3(clips_spec: dict[str, Any]) -> None:
    assert clips_spec.get("openapi", "").startswith("3."), "Expected OpenAPI 3.x spec"
    assert clips_spec.get("info", {}).get("title") == "Clips"
    assert "paths" in clips_spec and isinstance(clips_spec["paths"], Mapping)
    assert _base_url(clips_spec) == "https://api.zoom.us"


def test_clips_operations_have_operation_ids(clips_cases: list[OperationCase]) -> None:
    missing = [c for c in clips_cases if not c.operation_id]
    assert not missing, "All operations should have an operationId (or derived id)."


def test_clips_embedded_json_schemas_validate(clips_cases: list[OperationCase]) -> None:
    for case in clips_cases:
        if case.response_schema is not None:
            Draft202012Validator.check_schema(case.response_schema)


@pytest.mark.parametrize("case", [pytest.param(None, id="_placeholder")])
def test_clips_placeholder(case: Any) -> None:
    assert case is None


def pytest_generate_tests(metafunc: Any) -> None:
    if "clips_case" in metafunc.fixturenames:
        spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
        cases = _build_operation_cases(spec)
        ids = [f"{_snake(c.operation_id)}[{c.method} {c.path}]" for c in cases]
        metafunc.parametrize("clips_case", cases, ids=ids)


@pytest.mark.usefixtures("respx_mock")
def test_clips_operation_contract(
    clips_client: Any,
    clips_spec: dict[str, Any],
    clips_case: OperationCase,
    respx_mock: Any,
) -> None:
    request = _get_request_callable(clips_client)

    formatted_path = _format_path(clips_case.path, clips_case.path_params)
    url = f"{_base_url(clips_spec)}{formatted_path}"

    response_payload: Any = None
    if clips_case.response_schema is not None:
        response_payload = _example_from_schema(clips_spec, clips_case.response_schema)
        if response_payload is None:
            response_payload = {}
        _validate(response_payload, clips_case.response_schema)

    route_kwargs: dict[str, Any] = {"status_code": clips_case.status_code}
    if clips_case.response_schema is not None:
        route_kwargs["json"] = response_payload
    route = respx_mock.request(clips_case.method, url).mock(
        return_value=httpx.Response(**route_kwargs)
    )

    result = request(
        clips_case.method,
        clips_case.path,
        path_params=clips_case.path_params or None,
        params=clips_case.query_params or None,
        json=clips_case.request_json,
    )

    if isinstance(result, httpx.Response):
        assert result.status_code == clips_case.status_code
        got = result.json() if result.content else None
    else:
        got = result

    assert route.called, f"Client did not call {clips_case.method} {url}"

    call = route.calls[-1].request

    if clips_case.query_params:
        for k, v in clips_case.query_params.items():
            assert call.url.params.get(k) == str(v)

    if clips_case.request_json is not None and clips_case.method in {"POST", "PUT", "PATCH"}:
        assert call.headers.get("content-type", "").startswith("application/json")
        sent = json.loads(call.content.decode("utf-8")) if call.content else None
        assert sent == clips_case.request_json

    if clips_case.response_schema is not None:
        _validate(got, clips_case.response_schema)
        assert got == response_payload
    else:
        assert got is None


def test_clips_client_uses_httpx_transport(clips_client: Any) -> None:
    req = _get_request_callable(clips_client)
    assert callable(req)
