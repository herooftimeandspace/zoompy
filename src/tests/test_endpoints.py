"""Generic schema-driven contract tests for all bundled endpoint documents.

This module replaces the earlier pattern of one near-identical test file per
OpenAPI schema under `src/tests/endpoints/**`. The actual endpoint contract logic
is unchanged: we still load the real schema file, discover operations, generate
request/response examples, mock the outbound HTTP call with `respx`, and route
everything through the production `ZoomClient.request` method via pytest
fixtures.

The cleanup here is structural, not behavioral. Instead of maintaining dozens of
small wrappers that only differ by schema path and fixture name, we discover the
schema files dynamically and centralize the two real exceptions:

* `SCIM2` uses the `scim_client` fixture and needs an SCIM `Accept` header.
* `Workforce Management` keeps the existing `workforce_client` fixture name.

Everything else follows the same shared contract path the older modules already
used.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from _path_schema_suite import (
    build_parametrization,
    fixture_name_for_spec_path,
    load_titled_spec,
    request_headers_for_spec_path,
    schema_paths,
    spec_title,
)

from _openapi_contract import (
    build_operation_cases,
    get_request_callable,
    run_operation_contract,
    validate_response_examples,
)

SCHEMA_ROOT = Path(__file__).resolve().parent / "endpoints"


def _schema_paths() -> list[Path]:
    """Return the endpoint schema files mirrored into the test tree."""

    return schema_paths(SCHEMA_ROOT)


def _load_spec(path: Path) -> dict[str, Any]:
    """Load one endpoint OpenAPI file and require a non-empty document title."""

    return load_titled_spec(path, suite_label="Endpoint")


def _spec_title(spec: dict[str, Any]) -> str:
    """Return the OpenAPI document title from a loaded spec."""

    return spec_title(spec)


def _fixture_name_for_spec_path(path: Path) -> str:
    """Return the pytest fixture name that should service one endpoint family.

    We derive the default fixture name from the schema filename rather than the
    OpenAPI title text. That preserves existing names for acronym-heavy files
    like `QSS.json`, `AI Companion.json`, and `Video SDK.json` without needing
    a huge hand-maintained mapping table.
    """

    return fixture_name_for_spec_path(path)


def _request_headers_for_spec_path(path: Path) -> dict[str, str] | None:
    """Return any endpoint-family-specific request headers."""

    return request_headers_for_spec_path(path)


# Load each endpoint spec file as its own top-level pytest parameter.
@pytest.fixture(params=_schema_paths(), ids=lambda path: path.stem)
def endpoint_spec_path(request: pytest.FixtureRequest) -> Path:
    """Expose one endpoint schema path to the generic tests below."""

    return cast(Path, request.param)


# Materialize the OpenAPI document once so several tests can reuse it cheaply.
@pytest.fixture
def endpoint_spec(endpoint_spec_path: Path) -> dict[str, Any]:
    """Load one endpoint OpenAPI document from disk."""

    return _load_spec(endpoint_spec_path)


# Build the schema-derived contract cases once per spec file for reuse.
@pytest.fixture
def endpoint_cases(endpoint_spec: dict[str, Any]) -> list[Any]:
    """Build discovered endpoint operation cases for one schema file."""

    cases = build_operation_cases(endpoint_spec)
    if not cases:
        raise AssertionError("No operations discovered in endpoint OpenAPI spec.")
    return cases


# Confirm every schema file is still a real OpenAPI 3 document with path items.
def test_endpoint_spec_is_openapi_3(
    endpoint_spec_path: Path,
    endpoint_spec: dict[str, Any],
) -> None:
    assert endpoint_spec.get("openapi", "").startswith("3.")
    assert "paths" in endpoint_spec
    assert isinstance(endpoint_spec["paths"], dict)
    assert endpoint_spec["paths"], f"No paths declared in {endpoint_spec_path}"


# Operation IDs keep parametrized failures readable across the whole suite.
def test_endpoint_operations_have_operation_ids(endpoint_cases: list[Any]) -> None:
    assert not [case for case in endpoint_cases if not case.operation_id]


# Generated example responses should validate against every embedded response schema.
def test_endpoint_embedded_json_schemas_validate(
    endpoint_spec: dict[str, Any],
    endpoint_cases: list[Any],
) -> None:
    validate_response_examples(endpoint_spec, endpoint_cases)


def pytest_generate_tests(metafunc: Any) -> None:
    """Create one pytest case per documented endpoint operation across all specs.

    This keeps the generic module structurally parallel to the old per-file
    suites: every operation still gets its own pytest node, but discovery now
    comes from the schema tree instead of hand-written wrapper modules.
    """

    if "endpoint_case" not in metafunc.fixturenames:
        return

    parameters, ids = build_parametrization(SCHEMA_ROOT)

    metafunc.parametrize(
        (
            "endpoint_spec_path",
            "endpoint_spec",
            "endpoint_fixture_name",
            "endpoint_request_headers",
            "endpoint_case",
        ),
        parameters,
        ids=ids,
    )


# Execute the same shared request/response contract used by the old endpoint files.
@pytest.mark.usefixtures("respx_mock")
def test_endpoint_operation_contract(
    request: pytest.FixtureRequest,
    endpoint_spec: dict[str, Any],
    endpoint_fixture_name: str,
    endpoint_request_headers: dict[str, str] | None,
    endpoint_case: Any,
    respx_mock: Any,
) -> None:
    """Run the shared endpoint operation contract for one discovered case."""

    client_fixture = request.getfixturevalue(endpoint_fixture_name)
    run_operation_contract(
        request=get_request_callable(client_fixture, endpoint_fixture_name),
        spec=endpoint_spec,
        case=endpoint_case,
        respx_mock=respx_mock,
        request_headers=endpoint_request_headers,
    )


# Keep the old “fixture must return a callable” contract check explicit.
def test_endpoint_client_uses_callable_fixture(
    request: pytest.FixtureRequest,
    endpoint_spec_path: Path,
    endpoint_spec: dict[str, Any],
) -> None:
    """Ensure the fixture chosen for one endpoint family still satisfies the contract."""

    fixture_name = _fixture_name_for_spec_path(endpoint_spec_path)
    client_fixture = request.getfixturevalue(fixture_name)
    assert callable(get_request_callable(client_fixture, fixture_name))
