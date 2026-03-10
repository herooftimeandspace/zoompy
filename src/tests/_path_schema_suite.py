"""Shared helpers for path-based schema contract suites.

Both `test_endpoints.py` and `test_master_accounts.py` exercise ordinary
OpenAPI `paths` documents through the same shared contract runner. The only
real differences are:

* where the mirrored schema files live
* which label should appear in failure messages

This helper keeps those suites parallel without forcing each module to repeat
the same fixture-name logic, schema loading checks, and parametrization setup.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from _openapi_contract import (
    build_operation_cases,
    load_openapi_spec,
    snake_case,
)

FIXTURE_NAME_OVERRIDES = {
    "SCIM2": "scim_client",
    "Workforce Management": "workforce_client",
}

REQUEST_HEADERS_OVERRIDES = {
    "SCIM2": {"accept": "application/scim+json"},
}


def schema_paths(root: Path) -> list[Path]:
    """Return the mirrored JSON schema files under one suite root."""

    return sorted(root.rglob("*.json"))


def load_titled_spec(path: Path, *, suite_label: str) -> dict[str, Any]:
    """Load one OpenAPI document and require a non-empty `info.title`.

    The contract suites rely on the document title for readable pytest ids and
    for a few fixture-name conventions. Failing early here makes broken schema
    syncs much easier to diagnose than a later `KeyError` during parametrized
    test generation.
    """

    spec = load_openapi_spec(path)
    title = spec.get("info", {}).get("title")
    if not isinstance(title, str) or not title:
        raise AssertionError(f"{suite_label} spec at {path} is missing info.title.")
    return spec


def spec_title(spec: dict[str, Any]) -> str:
    """Return the document title used for readable pytest ids."""

    return str(spec.get("info", {}).get("title", "")).strip()


def fixture_name_for_spec_path(path: Path) -> str:
    """Return the historical fixture name for one schema family.

    Most fixtures follow `snake_case(file_stem) + "_client"`. A few older
    suites intentionally keep special names, so those remain centralized in the
    override table above.
    """

    stem = path.stem
    override = FIXTURE_NAME_OVERRIDES.get(stem)
    if override is not None:
        return override
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_").lower()
    return f"{normalized}_client"


def request_headers_for_spec_path(path: Path) -> dict[str, str] | None:
    """Return any schema-family-specific request headers."""

    headers = REQUEST_HEADERS_OVERRIDES.get(path.stem)
    if headers is None:
        return None
    return dict(headers)


def build_parametrization(root: Path) -> tuple[list[Any], list[str]]:
    """Build shared pytest parameters and ids for a path-based schema suite."""

    parameters: list[Any] = []
    ids: list[str] = []

    for spec_path in schema_paths(root):
        spec = load_titled_spec(spec_path, suite_label="Path-based")
        title = spec_title(spec)
        fixture_name = fixture_name_for_spec_path(spec_path)
        request_headers = request_headers_for_spec_path(spec_path)
        for case in build_operation_cases(spec):
            parameters.append((spec_path, spec, fixture_name, request_headers, case))
            ids.append(
                f"{snake_case(title)}:"
                f"{snake_case(case.operation_id)}[{case.method} {case.path}]"
            )

    return parameters, ids
