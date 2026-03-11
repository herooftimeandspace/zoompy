"""Schema-driven contract tests for the bundled Zoom webhook documents.

The repository already contains broad endpoint contract suites that exercise the
request side of the Zoom API against `src/tests/endpoints/**`. This module adds
the parallel coverage for webhook documents stored under `src/tests/webhooks/**`
without changing any of the existing endpoint-test logic.

Webhook specs are still OpenAPI documents, but the interesting contract is the
incoming payload Zoom delivers to subscribers. In practice that means this suite
focuses on:

1. verifying each webhook JSON file is a valid-looking OpenAPI document
2. discovering every documented webhook event
3. ensuring each webhook operation has a stable operation id
4. generating example event payloads from the published request-body schema
5. validating those generated payloads against the real schema

This keeps webhook coverage consistent with the endpoint suites while staying
deliberately read-only and implementation-agnostic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from _openapi_contract import (
    build_webhook_cases,
    load_openapi_spec,
    snake_case,
    validate_webhook_examples,
)

WEBHOOK_ROOT = Path(__file__).resolve().parent / "webhooks"


def _webhook_spec_paths() -> list[Path]:
    """Return the webhook schema files currently mirrored into the test tree.

    We intentionally discover files dynamically so the webhook suite grows
    automatically as `scripts/sync_schemas.py` downloads additional webhook
    documents. That keeps test maintenance low and avoids another layer of
    hard-coded file lists to update whenever Zoom adds a new product family.
    """

    return sorted(WEBHOOK_ROOT.rglob("*.json"))


def _load_spec(path: Path) -> dict[str, Any]:
    """Load one webhook spec using the same helper as the endpoint suites."""

    spec = load_openapi_spec(path)
    title = spec.get("info", {}).get("title")
    if not isinstance(title, str) or not title:
        raise AssertionError(f"Webhook spec at {path} is missing info.title.")
    return spec


def _spec_title(spec: dict[str, Any], path: Path) -> str:
    """Return the document title used for readable parametrized ids."""

    return str(spec.get("info", {}).get("title", path.stem))


def _build_webhook_parametrization() -> tuple[list[Any], list[str]]:
    """Build pytest parameters and ids for every mirrored webhook event."""

    parameters: list[Any] = []
    ids: list[str] = []
    for spec_path in _webhook_spec_paths():
        spec = _load_spec(spec_path)
        title = _spec_title(spec, spec_path)
        for case in build_webhook_cases(spec):
            parameters.append((spec_path, spec, case))
            ids.append(
                f"{snake_case(title)}:"
                f"{snake_case(case.operation_id)}[{case.method} {case.event_name}]"
            )
    return parameters, ids


# Load each mirrored webhook spec file as its own top-level pytest parameter.
@pytest.fixture(params=_webhook_spec_paths(), ids=lambda path: path.stem)
def webhook_spec_path(request: pytest.FixtureRequest) -> Path:
    """Expose one webhook schema path to the generic tests below."""

    return cast(Path, request.param)


# Materialize the JSON document so the tests do not each re-read the same file.
@pytest.fixture
def webhook_spec(webhook_spec_path: Path) -> dict[str, Any]:
    """Load one webhook OpenAPI document from disk."""

    return _load_spec(webhook_spec_path)


# Derive the webhook cases once per spec file for reuse across assertions.
@pytest.fixture
def webhook_cases(webhook_spec: dict[str, Any]) -> list[Any]:
    """Build the discovered webhook event cases for one webhook document."""

    cases = build_webhook_cases(webhook_spec)
    if not cases:
        raise AssertionError("No webhook operations discovered in webhook OpenAPI spec.")
    return cases


# Confirm each webhook file is a real OpenAPI document with a webhook section.
def test_webhook_spec_is_openapi_3(
    webhook_spec_path: Path,
    webhook_spec: dict[str, Any],
) -> None:
    assert webhook_spec.get("openapi", "").startswith("3.")
    assert "webhooks" in webhook_spec
    assert isinstance(webhook_spec["webhooks"], dict)
    assert webhook_spec["webhooks"], f"No webhooks declared in {webhook_spec_path}"


# Operation IDs are needed for readable failures when a specific event breaks.
def test_webhook_operations_have_operation_ids(webhook_cases: list[Any]) -> None:
    assert not [case for case in webhook_cases if not case.operation_id]


# Generated example payloads should validate against every documented webhook schema.
def test_webhook_embedded_json_schemas_validate(
    webhook_spec: dict[str, Any],
    webhook_cases: list[Any],
) -> None:
    validate_webhook_examples(webhook_spec, webhook_cases)


def pytest_generate_tests(metafunc: Any) -> None:
    """Create one pytest case per documented webhook event across all files.

    This mirrors the endpoint suites' pattern: instead of manually writing one
    test per webhook event, we discover every event from the schema files and
    let pytest provide readable ids like `meeting_started[POST meeting.started]`.
    """

    if "webhook_case" not in metafunc.fixturenames:
        return

    parameters, ids = _build_webhook_parametrization()

    metafunc.parametrize(
        ("webhook_spec_path", "webhook_spec", "webhook_case"),
        parameters,
        ids=ids,
    )


# Each discovered webhook event should have a schema-valid payload example.
def test_webhook_event_payload_contract(
    webhook_spec_path: Path,
    webhook_spec: dict[str, Any],
    webhook_case: Any,
) -> None:
    """Validate one webhook event payload contract.

    This test intentionally stays narrow. It does not try to emulate webhook
    delivery infrastructure or a user implementation's handler. Instead, it
    proves that for every documented webhook event in the mirrored schema tree,
    we can derive one payload that satisfies the exact request-body schema Zoom
    publishes.
    """

    validate_webhook_examples(webhook_spec, [webhook_case])
    assert webhook_case.request_schema
    assert webhook_case.method in {"POST", "PUT", "PATCH", "DELETE", "GET"}
    assert webhook_case.event_name
    assert webhook_spec_path.exists()
