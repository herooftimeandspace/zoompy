"""Generic contract tests for bundled Zoom master-account API documents.

This module is intentionally parallel to `test_endpoints.py`. Master-account
specs are still ordinary request/response OpenAPI documents with `paths`, so we
can reuse the same shared request-contract runner instead of inventing a second
testing model.

The only real difference is where the schema files live:

* ordinary endpoint specs are mirrored under `src/tests/endpoints/**`
* master-account specs are mirrored under `src/tests/master_accounts/**`

Keeping the suite separate makes failures easier to understand. When a Zoom
product family publishes both ordinary and master-account APIs, we want pytest
output to tell us which schema family failed without making the underlying
contract logic diverge.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from _openapi_contract import (
    build_operation_cases,
    get_request_callable,
    load_openapi_spec,
    run_operation_contract,
    snake_case,
    validate_response_examples,
)

MASTER_ACCOUNT_ROOT = Path(__file__).resolve().parent / "master_accounts"

# Master-account APIs still run through the same production request method, so
# they reuse the exact same fixture naming conventions as the ordinary endpoint
# suite. These small overrides preserve existing historical fixture names.
FIXTURE_NAME_OVERRIDES = {
    "SCIM2": "scim_client",
    "Workforce Management": "workforce_client",
}

REQUEST_HEADERS_OVERRIDES = {
    "SCIM2": {"accept": "application/scim+json"},
}


def _schema_paths() -> list[Path]:
    """Return the mirrored master-account schema files currently on disk."""

    return sorted(MASTER_ACCOUNT_ROOT.rglob("*.json"))


def _load_spec(path: Path) -> dict[str, Any]:
    """Load one master-account OpenAPI file and require a document title."""

    spec = load_openapi_spec(path)
    title = spec.get("info", {}).get("title")
    if not isinstance(title, str) or not title:
        raise AssertionError(
            f"Master-account spec at {path} is missing info.title."
        )
    return spec


def _spec_title(spec: dict[str, Any]) -> str:
    """Return the document title used for readable pytest ids."""

    return str(spec.get("info", {}).get("title", "")).strip()


def _fixture_name_for_spec_path(path: Path) -> str:
    """Return the pytest fixture name that should service one schema family."""

    stem = path.stem
    override = FIXTURE_NAME_OVERRIDES.get(stem)
    if override is not None:
        return override
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_").lower()
    return f"{normalized}_client"


def _request_headers_for_spec_path(path: Path) -> dict[str, str] | None:
    """Return any schema-family-specific request headers."""

    headers = REQUEST_HEADERS_OVERRIDES.get(path.stem)
    if headers is None:
        return None
    return dict(headers)


@pytest.fixture(params=_schema_paths(), ids=lambda path: path.stem)
def master_account_spec_path(request: pytest.FixtureRequest) -> Path:
    """Expose one master-account schema path to the generic tests below."""

    return request.param


@pytest.fixture
def master_account_spec(master_account_spec_path: Path) -> dict[str, Any]:
    """Load one master-account OpenAPI document from disk."""

    return _load_spec(master_account_spec_path)


@pytest.fixture
def master_account_cases(master_account_spec: dict[str, Any]) -> list[Any]:
    """Build the discovered operation cases for one master-account spec."""

    cases = build_operation_cases(master_account_spec)
    if not cases:
        raise AssertionError(
            "No operations discovered in master-account OpenAPI spec."
        )
    return cases


def test_master_account_spec_is_openapi_3(
    master_account_spec_path: Path,
    master_account_spec: dict[str, Any],
) -> None:
    """Confirm each mirrored file is a real OpenAPI 3 path document."""

    assert master_account_spec.get("openapi", "").startswith("3.")
    assert "paths" in master_account_spec
    assert isinstance(master_account_spec["paths"], dict)
    assert master_account_spec["paths"], (
        f"No paths declared in {master_account_spec_path}"
    )


def test_master_account_operations_have_operation_ids(
    master_account_cases: list[Any],
) -> None:
    """Require stable operation ids so parametrized failures stay readable."""

    assert not [case for case in master_account_cases if not case.operation_id]


def test_master_account_embedded_json_schemas_validate(
    master_account_spec: dict[str, Any],
    master_account_cases: list[Any],
) -> None:
    """Smoke-test generated response examples against each embedded schema."""

    validate_response_examples(master_account_spec, master_account_cases)


def pytest_generate_tests(metafunc: Any) -> None:
    """Create one pytest case per documented master-account operation."""

    if "master_account_case" not in metafunc.fixturenames:
        return

    parameters: list[Any] = []
    ids: list[str] = []
    for spec_path in _schema_paths():
        spec = _load_spec(spec_path)
        title = _spec_title(spec)
        fixture_name = _fixture_name_for_spec_path(spec_path)
        request_headers = _request_headers_for_spec_path(spec_path)
        for case in build_operation_cases(spec):
            parameters.append((spec_path, spec, fixture_name, request_headers, case))
            ids.append(
                f"{snake_case(title)}:"
                f"{snake_case(case.operation_id)}[{case.method} {case.path}]"
            )

    metafunc.parametrize(
        (
            "master_account_spec_path",
            "master_account_spec",
            "master_account_fixture_name",
            "master_account_request_headers",
            "master_account_case",
        ),
        parameters,
        ids=ids,
    )


@pytest.mark.usefixtures("respx_mock")
def test_master_account_operation_contract(
    request: pytest.FixtureRequest,
    master_account_spec: dict[str, Any],
    master_account_fixture_name: str,
    master_account_request_headers: dict[str, str] | None,
    master_account_case: Any,
    respx_mock: Any,
) -> None:
    """Run the shared request/response contract for one discovered case."""

    client_fixture = request.getfixturevalue(master_account_fixture_name)
    run_operation_contract(
        request=get_request_callable(
            client_fixture,
            master_account_fixture_name,
        ),
        spec=master_account_spec,
        case=master_account_case,
        respx_mock=respx_mock,
        request_headers=master_account_request_headers,
    )


def test_master_account_client_uses_callable_fixture(
    request: pytest.FixtureRequest,
    master_account_spec_path: Path,
) -> None:
    """Ensure the chosen client fixture still satisfies the callable contract."""

    fixture_name = _fixture_name_for_spec_path(master_account_spec_path)
    client_fixture = request.getfixturevalue(fixture_name)
    assert callable(get_request_callable(client_fixture, fixture_name))
