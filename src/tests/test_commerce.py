"""Contract tests for the Zoom Commerce (Business Services) API surface.

These tests are intentionally implementation-agnostic.

To run them against *your* client implementation, provide ONE of these fixtures in
`src/tests/conftest.py`:

1) `commerce_caller(client, operation_id: str, *, params: dict, body: Any) -> Any`
   A callable/fixture that invokes the correct client method for the given operation.

   Example implementation:

       @pytest.fixture
       def commerce_caller():
           def _call(client, operation_id, *, params=None, body=None):
               # route operation_id to your client's method
               return getattr(client.commerce, to_snake(operation_id))(**(params or {}), body=body)
               
           return _call

OR

2) `client_factory(transport) -> client`
   A callable/fixture that builds your client and accepts an injected transport.
   The tests will attempt best-effort method discovery based on OpenAPI operationId.

The tests verify:
- The client issues the correct HTTP method + path.
- Required path/query/header params are present.
- Request bodies minimally satisfy the OpenAPI schema.
- Returned JSON (when present) validates against the documented response schema.

Yes, humans could have written a stable interface contract up-front.
Instead we have this. You're welcome.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Tuple

import pytest

try:
    import jsonschema
except Exception as e:  # pragma: no cover
    raise RuntimeError(
        "jsonschema is required for schema contract tests. Add `jsonschema` to your test dependencies."
    ) from e


SCHEMA_PATH = Path("src/tests/schemas/business_services/Commerce.json")


# -----------------------------
# Helpers: OpenAPI parsing
# -----------------------------

def load_openapi() -> dict:
    if not SCHEMA_PATH.exists():
        raise AssertionError(
            f"Expected OpenAPI schema at {SCHEMA_PATH!s}. "
            "If you moved schemas again (because humans love doing that), update SCHEMA_PATH."
        )
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def iter_operations(spec: Mapping[str, Any]) -> Iterable[Tuple[str, str, str, dict]]:
    """Yield (operation_id, http_method, path, operation_obj)."""
    paths = spec.get("paths", {})
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, op in path_item.items():
            m = str(method).lower()
            if m not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not isinstance(op, dict):
                continue
            op_id = op.get("operationId") or f"{m}_{path.strip('/').replace('/', '_')}"
            yield str(op_id), m, str(path), op


def collect_parameters(spec: Mapping[str, Any], path: str, op: Mapping[str, Any]) -> List[dict]:
    """Collect parameters from path-item and operation level."""
    path_item = spec.get("paths", {}).get(path, {}) or {}
    params = []
    for bucket in (path_item.get("parameters", []), op.get("parameters", [])):
        if isinstance(bucket, list):
            params.extend([p for p in bucket if isinstance(p, dict)])
    return params


def resolve_ref(spec: Mapping[str, Any], obj: Any) -> Any:
    """Resolve a local #/components/... $ref if present."""
    if not isinstance(obj, dict):
        return obj
    ref = obj.get("$ref")
    if not ref:
        return obj
    if not isinstance(ref, str) or not ref.startswith("#/"):
        # Remote refs not supported in these offline contract tests.
        return obj
    cur: Any = spec
    for part in ref.lstrip("#/").split("/"):
        if not isinstance(cur, dict) or part not in cur:
            return obj
        cur = cur[part]
    return cur


def deep_resolve(spec: Mapping[str, Any], obj: Any) -> Any:
    """Resolve one-level $ref and recurse into obvious schema containers."""
    obj = resolve_ref(spec, obj)
    if isinstance(obj, dict):
        out = dict(obj)
        # Resolve nested refs in common OpenAPI schema locations
        for k in ("schema", "items", "allOf", "oneOf", "anyOf", "properties"):
            if k in out:
                if isinstance(out[k], list):
                    out[k] = [deep_resolve(spec, x) for x in out[k]]
                elif isinstance(out[k], dict):
                    out[k] = deep_resolve(spec, out[k])
        if "properties" in out and isinstance(out["properties"], dict):
            out["properties"] = {kk: deep_resolve(spec, vv) for kk, vv in out["properties"].items()}
        return out
    if isinstance(obj, list):
        return [deep_resolve(spec, x) for x in obj]
    return obj


def pick_response_schema(spec: Mapping[str, Any], op: Mapping[str, Any]) -> Tuple[Optional[dict], int]:
    """Pick the 'best' success response schema, preferring 200 then 201 then 204."""
    responses = op.get("responses", {}) or {}
    for code in ("200", "201", "202", "204"):
        if code not in responses:
            continue
        resp = responses[code] or {}
        if not isinstance(resp, dict):
            continue
        content = (resp.get("content") or {}).get("application/json")
        if isinstance(content, dict) and "schema" in content:
            schema = deep_resolve(spec, content["schema"])
            return schema, int(code)
        # 204 or schema-less responses
        return None, int(code)
    return None, 200


# -----------------------------
# Helpers: minimal example builder
# -----------------------------


def build_example(spec: Mapping[str, Any], schema: Any) -> Any:
    """Best-effort: build a minimal value that validates against an OpenAPI schema."""
    schema = deep_resolve(spec, schema)

    if not isinstance(schema, dict):
        return None

    if "example" in schema:
        return schema["example"]
    if "default" in schema:
        return schema["default"]
    if "enum" in schema and isinstance(schema["enum"], list) and schema["enum"]:
        return schema["enum"][0]

    # Composition
    if "allOf" in schema and isinstance(schema["allOf"], list) and schema["allOf"]:
        # merge object-ish allOf
        merged: Dict[str, Any] = {}
        for s in schema["allOf"]:
            v = build_example(spec, s)
            if isinstance(v, dict):
                merged.update(v)
        return merged or build_example(spec, schema["allOf"][0])

    t = schema.get("type")

    if t == "object" or (t is None and "properties" in schema):
        props = schema.get("properties", {}) or {}
        required = schema.get("required", []) or []
        out: Dict[str, Any] = {}
        for name in required:
            if name in props:
                out[name] = build_example(spec, props[name])
        # If nothing required, add at least one property to avoid returning {} for everything
        if not out and props:
            first_key = next(iter(props.keys()))
            out[first_key] = build_example(spec, props[first_key])
        return out

    if t == "array":
        item_schema = schema.get("items", {})
        min_items = schema.get("minItems", 0) or 0
        n = max(1, int(min_items))
        return [build_example(spec, item_schema) for _ in range(n)]

    if t == "string":
        fmt = schema.get("format")
        if fmt == "date" or fmt == "YYYY-MM-DD":
            return "2025-01-01"
        if fmt == "date-time":
            return "2025-01-01T00:00:00Z"
        if fmt == "uuid":
            return "00000000-0000-0000-0000-000000000000"
        max_len = schema.get("maxLength")
        min_len = schema.get("minLength")
        base = "test"
        if isinstance(min_len, int) and min_len > len(base):
            base = base + ("x" * (min_len - len(base)))
        if isinstance(max_len, int) and max_len < len(base):
            base = base[:max_len]
        return base

    if t == "integer":
        if "minimum" in schema:
            return int(schema["minimum"])
        return 1

    if t == "number":
        if "minimum" in schema:
            return float(schema["minimum"])
        return 1.0

    if t == "boolean":
        return True

    # fall back
    return None


def to_snake(name: str) -> str:
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    name = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", name)
    return name.replace("-", "_").lower()


# -----------------------------
# Helpers: a minimal transport spy
# -----------------------------


@dataclass
class _ResponseStub:
    status_code: int
    payload: Any = None
    headers: Mapping[str, str] | None = None

    def json(self) -> Any:
        return self.payload

    @property
    def text(self) -> str:
        try:
            return json.dumps(self.payload)
        except Exception:
            return str(self.payload)


class TransportSpy:
    """A tiny transport that captures outbound requests.

    Your client should accept *some* injectable transport or session object.
    If it doesn't, that's not a test failure, it's a design failure.
    """

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._next_response: _ResponseStub = _ResponseStub(200, {})

    def queue_response(self, status_code: int, payload: Any = None, headers: Mapping[str, str] | None = None) -> None:
        self._next_response = _ResponseStub(status_code=status_code, payload=payload, headers=headers or {})

    # Support common client patterns: requests.Session.request, httpx.Client.request, custom transports, etc.
    def request(self, method: str, url: str, **kwargs: Any) -> _ResponseStub:  # type: ignore[override]
        self.calls.append({"method": method.lower(), "url": url, **kwargs})
        return self._next_response

    def __call__(self, method: str, url: str, **kwargs: Any) -> _ResponseStub:
        return self.request(method, url, **kwargs)


# -----------------------------
# Fixtures: plug in your client
# -----------------------------


@pytest.fixture
def transport() -> TransportSpy:
    return TransportSpy()


@pytest.fixture
def client(client_factory: Callable[[TransportSpy], Any], transport: TransportSpy) -> Any:
    """Default client fixture.

    Implement `client_factory` in your conftest to construct your API client.
    """
    return client_factory(transport)


@pytest.fixture
def commerce_caller() -> Optional[Callable[..., Any]]:
    """Optional override for calling operations.

    If you provide a fixture with this same name in your conftest, pytest will override this.
    """
    return None


# -----------------------------
# Contract tests
# -----------------------------


_SPEC = load_openapi()
_OPERATIONS = list(iter_operations(_SPEC))


@pytest.mark.parametrize("operation_id, http_method, path, op", _OPERATIONS)
def test_commerce_operation_is_callable(client: Any, commerce_caller: Optional[Callable[..., Any]], operation_id: str, http_method: str, path: str, op: dict):
    """Every OpenAPI operationId should map to something callable on the client."""

    if commerce_caller is not None:
        # If the project provides a caller, assume it knows how to route.
        return

    snake = to_snake(operation_id)

    # Best effort discovery: client.commerce.<op>() or client.<op>()
    target = None
    if hasattr(client, "commerce") and hasattr(getattr(client, "commerce"), snake):
        target = getattr(getattr(client, "commerce"), snake)
    elif hasattr(client, snake):
        target = getattr(client, snake)

    assert callable(target), (
        f"No callable found for operationId '{operation_id}'. Expected one of:\n"
        f"- client.commerce.{snake}(...)\n"
        f"- client.{snake}(...)\n\n"
        "If your client uses a different structure, provide a `commerce_caller` fixture in conftest.py "
        "that can invoke operations by operation_id."
    )


@pytest.mark.parametrize("operation_id, http_method, path, op", _OPERATIONS)
def test_commerce_sends_correct_http_method_and_path(
    client: Any,
    transport: TransportSpy,
    commerce_caller: Optional[Callable[..., Any]],
    operation_id: str,
    http_method: str,
    path: str,
    op: dict,
):
    """Calling an operation should result in a single outbound request with the documented method+path."""

    params = collect_parameters(_SPEC, path, op)

    path_params: Dict[str, Any] = {}
    query_params: Dict[str, Any] = {}
    header_params: Dict[str, Any] = {}

    for p in params:
        p = deep_resolve(_SPEC, p)
        where = p.get("in")
        name = p.get("name")
        required = bool(p.get("required"))
        schema = p.get("schema", {})

        if not name or where not in {"path", "query", "header"}:
            continue

        if required:
            value = build_example(_SPEC, schema)
            if where == "path":
                path_params[name] = value
            elif where == "query":
                query_params[name] = value
            elif where == "header":
                header_params[name] = value

    body = None
    request_body = op.get("requestBody")
    if isinstance(request_body, dict):
        request_body = deep_resolve(_SPEC, request_body)
        required = bool(request_body.get("required"))
        content = (request_body.get("content") or {}).get("application/json")
        if required and isinstance(content, dict) and "schema" in content:
            body = build_example(_SPEC, content["schema"])

    # Prepare the stubbed response (if any)
    resp_schema, status = pick_response_schema(_SPEC, op)
    if resp_schema is None:
        transport.queue_response(status, None)
    else:
        transport.queue_response(status, build_example(_SPEC, resp_schema))

    # Invoke
    call_kwargs = {**path_params, **query_params}

    if commerce_caller is not None:
        commerce_caller(client, operation_id, params={**call_kwargs, "headers": header_params}, body=body)
    else:
        func = None
        snake = to_snake(operation_id)
        if hasattr(client, "commerce") and hasattr(getattr(client, "commerce"), snake):
            func = getattr(getattr(client, "commerce"), snake)
        elif hasattr(client, snake):
            func = getattr(client, snake)
        assert callable(func), "Callability is validated by a separate test."

        # Try common calling conventions.
        try:
            func(**call_kwargs, headers=header_params, body=body)
        except TypeError:
            # Some clients prefer json= or data= over body=
            try:
                func(**call_kwargs, headers=header_params, json=body)
            except TypeError:
                # Some clients expect params={...}
                func(params=call_kwargs, headers=header_params, json=body)

    assert transport.calls, "Client did not make any outbound HTTP request."
    assert len(transport.calls) == 1, f"Expected exactly 1 outbound request, saw {len(transport.calls)}."

    call = transport.calls[0]
    assert call.get("method") == http_method, f"Expected method {http_method}, got {call.get('method')}"

    # URL should include the documented path, with path params substituted.
    expected_suffix = path
    for k, v in path_params.items():
        expected_suffix = expected_suffix.replace("{" + k + "}", str(v))

    url = str(call.get("url", ""))
    assert expected_suffix in url, f"Expected URL to contain path '{expected_suffix}', got '{url}'"


@pytest.mark.parametrize("operation_id, http_method, path, op", _OPERATIONS)
def test_commerce_required_query_and_headers_are_present(
    client: Any,
    transport: TransportSpy,
    commerce_caller: Optional[Callable[..., Any]],
    operation_id: str,
    http_method: str,
    path: str,
    op: dict,
):
    """If OpenAPI marks query/header params required, the request must include them."""

    params = collect_parameters(_SPEC, path, op)

    required_query: List[str] = []
    required_headers: List[str] = []
    path_params: Dict[str, Any] = {}
    query_params: Dict[str, Any] = {}
    header_params: Dict[str, Any] = {}

    for p in params:
        p = deep_resolve(_SPEC, p)
        where = p.get("in")
        name = p.get("name")
        required = bool(p.get("required"))
        schema = p.get("schema", {})
        if not name:
            continue
        if where == "path" and required:
            path_params[name] = build_example(_SPEC, schema)
        if where == "query" and required:
            required_query.append(name)
            query_params[name] = build_example(_SPEC, schema)
        if where == "header" and required:
            required_headers.append(name)
            header_params[name] = build_example(_SPEC, schema)

    resp_schema, status = pick_response_schema(_SPEC, op)
    transport.queue_response(status, build_example(_SPEC, resp_schema) if resp_schema else None)

    call_kwargs = {**path_params, **query_params}

    if commerce_caller is not None:
        commerce_caller(client, operation_id, params={**call_kwargs, "headers": header_params}, body=None)
    else:
        snake = to_snake(operation_id)
        func = None
        if hasattr(client, "commerce") and hasattr(getattr(client, "commerce"), snake):
            func = getattr(getattr(client, "commerce"), snake)
        elif hasattr(client, snake):
            func = getattr(client, snake)
        assert callable(func), "Callability is validated by a separate test."
        try:
            func(**call_kwargs, headers=header_params)
        except TypeError:
            func(params=call_kwargs, headers=header_params)

    call = transport.calls[0]

    # Common ways clients pass query params
    sent_query = call.get("params") or call.get("query") or {}
    if not isinstance(sent_query, dict):
        sent_query = {}

    sent_headers = call.get("headers") or {}
    if not isinstance(sent_headers, dict):
        sent_headers = {}

    for q in required_query:
        assert q in sent_query, f"Missing required query param '{q}' for operation '{operation_id}'."

    for h in required_headers:
        # Header keys are case-insensitive
        lowered = {str(k).lower() for k in sent_headers.keys()}
        assert h.lower() in lowered, f"Missing required header '{h}' for operation '{operation_id}'."


@pytest.mark.parametrize("operation_id, http_method, path, op", _OPERATIONS)
def test_commerce_request_body_validates_against_schema(
    client: Any,
    transport: TransportSpy,
    commerce_caller: Optional[Callable[..., Any]],
    operation_id: str,
    http_method: str,
    path: str,
    op: dict,
):
    """When an operation defines a JSON requestBody schema, the client must send a body that validates."""

    request_body = op.get("requestBody")
    if not isinstance(request_body, dict):
        pytest.skip("No requestBody")

    request_body = deep_resolve(_SPEC, request_body)
    content = (request_body.get("content") or {}).get("application/json")
    if not isinstance(content, dict) or "schema" not in content:
        pytest.skip("No application/json schema")

    schema = deep_resolve(_SPEC, content["schema"])
    example_body = build_example(_SPEC, schema)

    # Set up required path/query so call doesn't crash before it sends
    params = collect_parameters(_SPEC, path, op)
    path_params: Dict[str, Any] = {}
    query_params: Dict[str, Any] = {}
    header_params: Dict[str, Any] = {}

    for p in params:
        p = deep_resolve(_SPEC, p)
        where = p.get("in")
        name = p.get("name")
        required = bool(p.get("required"))
        if not (name and required):
            continue
        val = build_example(_SPEC, p.get("schema", {}))
        if where == "path":
            path_params[name] = val
        elif where == "query":
            query_params[name] = val
        elif where == "header":
            header_params[name] = val

    resp_schema, status = pick_response_schema(_SPEC, op)
    transport.queue_response(status, build_example(_SPEC, resp_schema) if resp_schema else None)

    call_kwargs = {**path_params, **query_params}

    if commerce_caller is not None:
        commerce_caller(client, operation_id, params={**call_kwargs, "headers": header_params}, body=example_body)
    else:
        snake = to_snake(operation_id)
        func = None
        if hasattr(client, "commerce") and hasattr(getattr(client, "commerce"), snake):
            func = getattr(getattr(client, "commerce"), snake)
        elif hasattr(client, snake):
            func = getattr(client, snake)
        assert callable(func), "Callability is validated by a separate test."

        try:
            func(**call_kwargs, headers=header_params, body=example_body)
        except TypeError:
            func(**call_kwargs, headers=header_params, json=example_body)

    call = transport.calls[0]

    sent_body = None
    for key in ("json", "body", "data"):
        if key in call:
            sent_body = call[key]
            break

    assert sent_body is not None, f"Operation '{operation_id}' defines a requestBody but no JSON body was sent."

    jsonschema.validate(instance=sent_body, schema=schema)


@pytest.mark.parametrize("operation_id, http_method, path, op", _OPERATIONS)
def test_commerce_response_validates_against_schema(
    client: Any,
    transport: TransportSpy,
    commerce_caller: Optional[Callable[..., Any]],
    operation_id: str,
    http_method: str,
    path: str,
    op: dict,
):
    """If OpenAPI documents a JSON response schema, whatever the client returns should validate."""

    resp_schema, status = pick_response_schema(_SPEC, op)
    if resp_schema is None:
        pytest.skip("No documented application/json response schema")

    expected_payload = build_example(_SPEC, resp_schema)
    transport.queue_response(status, expected_payload)

    # Required params to execute
    params = collect_parameters(_SPEC, path, op)
    path_params: Dict[str, Any] = {}
    query_params: Dict[str, Any] = {}
    header_params: Dict[str, Any] = {}

    for p in params:
        p = deep_resolve(_SPEC, p)
        where = p.get("in")
        name = p.get("name")
        required = bool(p.get("required"))
        if not (name and required):
            continue
        val = build_example(_SPEC, p.get("schema", {}))
        if where == "path":
            path_params[name] = val
        elif where == "query":
            query_params[name] = val
        elif where == "header":
            header_params[name] = val

    body = None
    request_body = op.get("requestBody")
    if isinstance(request_body, dict):
        request_body = deep_resolve(_SPEC, request_body)
        required = bool(request_body.get("required"))
        content = (request_body.get("content") or {}).get("application/json")
        if required and isinstance(content, dict) and "schema" in content:
            body = build_example(_SPEC, content["schema"])

    call_kwargs = {**path_params, **query_params}

    if commerce_caller is not None:
        result = commerce_caller(client, operation_id, params={**call_kwargs, "headers": header_params}, body=body)
    else:
        snake = to_snake(operation_id)
        func = None
        if hasattr(client, "commerce") and hasattr(getattr(client, "commerce"), snake):
            func = getattr(getattr(client, "commerce"), snake)
        elif hasattr(client, snake):
            func = getattr(client, snake)
        assert callable(func), "Callability is validated by a separate test."
        try:
            result = func(**call_kwargs, headers=header_params, body=body)
        except TypeError:
            result = func(**call_kwargs, headers=header_params, json=body)

    # Allow clients to return response objects, dicts, or None
    if result is None:
        pytest.skip("Client returned None for an operation with a documented JSON response; implementation may be returning a raw response.")

    # If they returned a response-like object, extract JSON
    if hasattr(result, "json") and callable(getattr(result, "json")):
        result_payload = result.json()
    else:
        result_payload = result

    jsonschema.validate(instance=result_payload, schema=resp_schema)


def test_commerce_schema_contains_expected_paths():
    """Sanity check that the schema we are testing actually looks like Zoom Commerce."""

    paths = _SPEC.get("paths", {})
    assert "/commerce/accounts" in paths, "Schema appears wrong: missing /commerce/accounts"  # sanity
    assert "/commerce/order" in paths, "Schema appears wrong: missing /commerce/order"  # sanity

