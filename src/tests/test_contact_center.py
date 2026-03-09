

"""Contract tests for the Zoom Contact Center API surface.

These tests are *schema-driven*.

They validate that an implementation:

1) Loads and uses the OpenAPI spec at:
   src/tests/schemas/build_platform/Contact Center.json
2) Can build correct requests for a representative set of Contact Center operations.
3) Can validate responses against the spec (at least for operations with non-trivial required fields).

To make these tests pass, provide a pytest fixture named `contact_center_api` that returns an object
with this minimum interface:

- `build_request(operation_id: str, **kwargs) -> dict`
    Must return a mapping with (at minimum) keys:
      - method: str   ("get", "post", "patch", "put", "delete")
      - path: str     (fully formatted path with any path params substituted)
      - params: dict  (query params)
      - json: Any     (JSON body) or None
      - headers: dict (headers)

- `validate_response(operation_id: str, status_code: int, payload: object) -> None`
    Must raise an exception on validation failure.

- Optional but recommended:
  - `spec` attribute containing the parsed OpenAPI document.

Because humans love ambiguity, operationIds in Zoom specs are not consistently cased.
These tests use the operationId strings *exactly* as provided by the schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Tuple

import json
import pytest


SCHEMA_PATH = (
    Path(__file__).resolve().parent
    / "schemas"
    / "build_platform"
    / "Contact Center.json"
)


def _load_spec() -> dict:
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(
            f"Contact Center OpenAPI schema not found at: {SCHEMA_PATH}. "
            "(Yes, the filename has a space. Humans did this.)"
        )
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _iter_operations(spec: Mapping[str, Any]) -> Iterable[Tuple[str, str, str, Mapping[str, Any]]]:
    """Yield (operation_id, method, path, operation_obj)."""
    for path, methods in spec.get("paths", {}).items():
        for method, op in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            op_id = op.get("operationId")
            if not op_id:
                continue
            yield op_id, method.lower(), path, op


def _op(spec: Mapping[str, Any], operation_id: str) -> Tuple[str, str, Mapping[str, Any]]:
    """Return (method, path_template, op) for an operationId."""
    for op_id, method, path, op in _iter_operations(spec):
        if op_id == operation_id:
            return method, path, op
    raise KeyError(f"operationId not found in spec: {operation_id}")


def _path_params(op: Mapping[str, Any]) -> Dict[str, Any]:
    params = {}
    for p in op.get("parameters", []) or []:
        if p.get("in") == "path" and p.get("required") is True:
            # Use an example if present; otherwise a boring placeholder string.
            params[p["name"]] = p.get("schema", {}).get("example") or "example"
    return params


def _required_query_params(op: Mapping[str, Any]) -> Dict[str, Any]:
    params = {}
    for p in op.get("parameters", []) or []:
        if p.get("in") == "query" and p.get("required") is True:
            params[p["name"]] = p.get("schema", {}).get("example")
            if params[p["name"]] is None:
                # Give something type-ish.
                t = (p.get("schema", {}) or {}).get("type")
                params[p["name"]] = 1 if t == "integer" else "example"
    return params


@dataclass(frozen=True)
class RequestExpectation:
    operation_id: str
    method: str
    path: str
    # Only include required query params here.
    required_params: Dict[str, Any]


# A focused set of operations that cover:
# - required query params
# - required path params
# - request bodies
# - and a grab bag of HTTP verbs.
EXPECTATIONS: Tuple[RequestExpectation, ...] = (
    # GET with required query param (unit_id)
    RequestExpectation(
        operation_id="listAddressBooks",
        method="get",
        path="/contact_center/address_books",
        required_params={"unit_id": "example"},
    ),
    # POST with JSON body
    RequestExpectation(
        operation_id="createAddressBook",
        method="post",
        path="/contact_center/address_books",
        required_params={},
    ),
    # GET with path param
    RequestExpectation(
        operation_id="Getaaddressbookcustomfield",
        method="get",
        path="/contact_center/address_books/custom_fields/{customFieldId}",
        required_params={},
    ),
    # PATCH with path param + JSON body
    RequestExpectation(
        operation_id="Updateacustomfield",
        method="patch",
        path="/contact_center/address_books/custom_fields/{customFieldId}",
        required_params={},
    ),
    # PUT with two path params
    RequestExpectation(
        operation_id="engagementRecordingControl",
        method="put",
        path="/contact_center/engagements/{engagementId}/recording/{command}",
        required_params={},
    ),
    # GET with path param + optional pagination
    RequestExpectation(
        operation_id="Listuserdevices",
        method="get",
        path="/contact_center/users/{userId}/devices",
        required_params={},
    ),
    # GET with many optional query params (we don't enforce them, just ensure it's wired)
    RequestExpectation(
        operation_id="Listhistoricalagentperformancedatasetdata",
        method="get",
        path="/contact_center/analytics/dataset/historical/agent_performance",
        required_params={},
    ),
)


def test_contact_center_schema_is_openapi_3() -> None:
    spec = _load_spec()

    assert spec.get("openapi", "").startswith("3."), "Expected an OpenAPI 3.x document"

    servers = spec.get("servers") or []
    assert servers, "Expected at least one server entry"
    # Zoom's Contact Center API is under /v2.
    assert any("/v2" in (s.get("url") or "") for s in servers), "Expected /v2 server URL"

    paths = spec.get("paths") or {}
    assert paths, "Expected non-empty paths"
    assert all(str(p).startswith("/contact_center") for p in paths.keys())


@pytest.mark.parametrize("exp", EXPECTATIONS, ids=lambda e: e.operation_id)
def test_build_request_matches_schema(contact_center_api: Any, exp: RequestExpectation) -> None:
    spec = getattr(contact_center_api, "spec", None) or _load_spec()

    method, path_tmpl, op = _op(spec, exp.operation_id)

    assert method == exp.method, f"Spec mismatch for {exp.operation_id}: method"
    assert path_tmpl == exp.path, f"Spec mismatch for {exp.operation_id}: path"

    # Build kwargs: required path params + required query params.
    kwargs: Dict[str, Any] = {}
    kwargs.update(_path_params(op))
    kwargs.update(_required_query_params(op))

    # If the endpoint has a requestBody, give it *something*.
    if "requestBody" in op:
        # Implementations can choose body arg name. We standardize on `json`.
        # If you prefer `data` or `payload`, map it internally.
        kwargs["json"] = {"example": True}

    req = contact_center_api.build_request(exp.operation_id, **kwargs)

    # Shape checks.
    assert isinstance(req, Mapping)
    for k in ("method", "path", "params", "json", "headers"):
        assert k in req, f"build_request() must return key: {k}"

    assert str(req["method"]).lower() == exp.method

    # Path substitution checks.
    if kwargs and any(k in kwargs for k in _path_params(op).keys()):
        # Ensure no unreplaced {param} remains.
        assert "{" not in str(req["path"]), "Path params were not substituted"
        assert "}" not in str(req["path"]), "Path params were not substituted"
    else:
        assert str(req["path"]) == exp.path

    # Required query params must land in params.
    params = req.get("params") or {}
    assert isinstance(params, Mapping)
    for k in exp.required_params.keys():
        assert k in params, f"Missing required query param in request: {k}"


def test_build_request_rejects_missing_required_query_param(contact_center_api: Any) -> None:
    """`listAddressBooks` requires `unit_id` as a query param (per schema)."""
    spec = getattr(contact_center_api, "spec", None) or _load_spec()
    _method, _path, op = _op(spec, "listAddressBooks")

    # sanity: the schema really says it's required
    required_q = _required_query_params(op)
    assert "unit_id" in required_q

    with pytest.raises(Exception):
        # Missing required unit_id should be rejected by your implementation.
        contact_center_api.build_request("listAddressBooks")


def test_validate_response_accepts_and_rejects_payloads(contact_center_api: Any) -> None:
    """Response validation should follow schema-required fields.

    We use `Createacustomfield` because its 201 response has non-trivial required fields.
    """
    valid = {
        "custom_field_id": "7381d210-360e-4fbd-86e4-33eb7109b084",
        "custom_field_name": "Preferred Contact Method",
        "data_type": "pick_list",
    }

    # Should not raise.
    contact_center_api.validate_response("Createacustomfield", 201, valid)

    invalid = {
        "custom_field_id": "7381d210-360e-4fbd-86e4-33eb7109b084",
        # missing custom_field_name
        "data_type": "pick_list",
    }

    with pytest.raises(Exception):
        contact_center_api.validate_response("Createacustomfield", 201, invalid)


def test_validate_response_rejects_unknown_operation(contact_center_api: Any) -> None:
    with pytest.raises(Exception):
        contact_center_api.validate_response("definitelyNotARealOperationId", 200, {})


def test_validate_response_rejects_unknown_status_code(contact_center_api: Any) -> None:
    # The spec won't define *every* random status code. Your validator should complain.
    with pytest.raises(Exception):
        contact_center_api.validate_response("Createacustomfield", 599, {})