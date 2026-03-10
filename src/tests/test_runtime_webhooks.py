"""Generic runtime webhook validation tests for the production client API.

The existing webhook suite proves that the mirrored webhook documents are
internally coherent and that our helper layer can synthesize payload examples
for every documented event. This companion module closes the remaining gap by
feeding those same example payloads through the real runtime webhook validator
implemented in `ZoomClient.validate_webhook()`.

That distinction matters. A helper-driven test can prove the schemas are
usable, but only a production-path test proves that the public client API loads
the bundled webhook registry correctly and can resolve event lookups at
runtime.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from _openapi_contract import (
    build_webhook_cases,
    conform_example_to_schema,
    example_from_schema,
    load_openapi_spec,
    snake_case,
)
from zoompy import ZoomClient, WebhookRegistry


WEBHOOK_ROOT = Path(__file__).resolve().parent / "webhooks"


def _webhook_spec_paths() -> list[Path]:
    """Return the mirrored webhook schema files used by the generic suite."""

    return sorted(WEBHOOK_ROOT.rglob("*.json"))


def _load_spec(path: Path) -> dict[str, Any]:
    """Load one mirrored webhook document and require a usable title."""

    spec = load_openapi_spec(path)
    title = spec.get("info", {}).get("title")
    if not isinstance(title, str) or not title:
        raise AssertionError(f"Webhook spec at {path} is missing info.title.")
    return spec


@pytest.fixture(scope="session")
def runtime_webhook_registry() -> WebhookRegistry:
    """Expose one production webhook registry for the generic runtime suite."""

    return WebhookRegistry()


def _payload_for_webhook_case(
    registry: WebhookRegistry,
    spec: dict[str, Any],
    schema_name: str,
    case: Any,
) -> Any:
    """Build one runtime-ready payload for a discovered webhook event.

    We intentionally mirror the same preference order used by the helper suite:

    1. start from a media-level example when the document provides one
    2. conform that example to the request-body schema
    3. fall back to synthetic schema-driven generation when needed

    This keeps the runtime validation suite aligned with the broader webhook
    contract tests instead of introducing a second payload-generation strategy.
    """

    candidates: list[Any] = []

    if case.request_example is not None:
        candidates.append(
            conform_example_to_schema(spec, case.request_example, case.request_schema)
        )

    generated = example_from_schema(spec, case.request_schema)
    candidates.append(conform_example_to_schema(spec, generated, case.request_schema))

    # Some webhook schemas accept a leaner synthetic payload than the richer
    # media-level example. We try both shapes and keep the first payload that
    # the production registry itself accepts.
    for candidate in candidates:
        try:
            registry.validate_webhook(
                event_name=case.event_name,
                payload=candidate,
                schema_name=schema_name,
                operation_id=case.operation_id,
            )
            return candidate
        except ValueError:
            continue

    raise AssertionError(
        f"Could not generate a runtime-valid webhook payload for "
        f"{case.operation_id} ({case.event_name})."
    )


def pytest_generate_tests(metafunc: Any) -> None:
    """Create one runtime validation case per documented webhook event."""

    if "runtime_webhook_case" not in metafunc.fixturenames:
        return

    parameters: list[Any] = []
    ids: list[str] = []
    for spec_path in _webhook_spec_paths():
        spec = _load_spec(spec_path)
        title = str(spec.get("info", {}).get("title", spec_path.stem))
        for case in build_webhook_cases(spec):
            parameters.append((title, spec, case))
            ids.append(
                f"{snake_case(title)}:"
                f"{snake_case(case.operation_id)}[{case.method} {case.event_name}]"
            )

    metafunc.parametrize(
        ("runtime_webhook_schema_name", "runtime_webhook_spec", "runtime_webhook_case"),
        parameters,
        ids=ids,
    )


def test_zoom_client_runtime_webhook_validation(
    zoom_client: ZoomClient,
    runtime_webhook_registry: WebhookRegistry,
    runtime_webhook_schema_name: str,
    runtime_webhook_spec: dict[str, Any],
    runtime_webhook_case: Any,
) -> None:
    """Validate each discovered webhook payload through the public client API.

    We pass both `schema_name` and `operation_id` so the production registry
    exercises its full lookup path without relying on event-name uniqueness
    across all bundled webhook documents.
    """

    runtime_webhook_payload = _payload_for_webhook_case(
        runtime_webhook_registry,
        runtime_webhook_spec,
        runtime_webhook_schema_name,
        runtime_webhook_case,
    )

    zoom_client.validate_webhook(
        runtime_webhook_case.event_name,
        runtime_webhook_payload,
        schema_name=runtime_webhook_schema_name,
        operation_id=runtime_webhook_case.operation_id,
    )
