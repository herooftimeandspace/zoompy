"""Focused tests for schema helper branches that broad contract suites skip."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from zoom_sdk.schema import OpenApiSchemaTools, PathOperationIndex, WebhookRegistry


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write one JSON schema document into a temporary test tree."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_pick_json_media_supports_generic_json_like_media_types() -> None:
    """Fall back to any media type whose name still contains `json`."""

    tools = OpenApiSchemaTools()

    media = tools.pick_json_media(
        {
            "application/vnd.zoom+json": {"schema": {"type": "object"}},
            "text/plain": {"schema": {"type": "string"}},
        }
    )

    assert media == {"schema": {"type": "object"}}


def test_resolve_ref_rejects_non_local_and_unresolvable_refs() -> None:
    """Raise clear errors for unsupported or broken references."""

    tools = OpenApiSchemaTools()
    spec = {"components": {"schemas": {"User": {"type": "object"}}}}

    with pytest.raises(ValueError, match="Only local refs"):
        tools.resolve_ref(spec, "https://example.invalid/schema.json")

    with pytest.raises(ValueError, match="Unresolvable"):
        tools.resolve_ref(spec, "#/components/schemas/Missing")


def test_path_operation_index_finds_exact_actual_and_regex_matches(tmp_path: Path) -> None:
    """Exercise the three lookup passes in `find_operation`."""

    _write_json(
        tmp_path / "endpoints" / "accounts" / "Users.json",
        {
            "openapi": "3.0.0",
            "info": {"title": "Users"},
            "servers": [
                {"description": "bad entry"},
                {"url": ""},
                {"url": "https://api.zoom.us/v2"},
            ],
            "paths": {
                "/users/me": {
                    "get": {
                        "operationId": "getCurrentUser",
                        "responses": {"200": {"description": "ok"}},
                    }
                },
                "/users/{userId}": {
                    "get": {
                        "operationId": "getUser",
                        "responses": {"200": {"description": "ok"}},
                    }
                },
                "/users/bad": [],
            },
        },
    )
    index = PathOperationIndex(resource_root=tmp_path, path_root_names=("endpoints",))

    exact = index.find_operation(
        method="GET",
        raw_path="/users/me",
        actual_path="/users/me",
    )
    actual = index.find_operation(
        method="GET",
        raw_path="/users/123",
        actual_path="/users/123",
    )
    regex = index.find_operation(
        method="GET",
        raw_path="/users/{userId}",
        actual_path="/users/abc",
    )

    assert exact.operation_id == "getCurrentUser"
    assert actual.operation_id == "getUser"
    assert regex.operation_id == "getUser"
    assert index.base_url_for_request(
        method="GET",
        raw_path="/missing",
        actual_path="/missing",
        fallback="https://fallback.example/",
    ) == "https://fallback.example"


def test_webhook_registry_rejects_missing_schemas_and_non_json_request_bodies(
    tmp_path: Path,
) -> None:
    """Cover missing-event errors and request-body extraction fallbacks."""

    _write_json(
        tmp_path / "webhooks" / "workplace" / "Meetings.json",
        {
            "openapi": "3.1.0",
            "info": {"title": "Meetings"},
            "webhooks": {
                "meeting.started": {
                    "post": {
                        "requestBody": {
                            "content": {
                                "text/plain": {"schema": {"type": "string"}}
                            }
                        }
                    }
                },
                "meeting.ended": [],
            },
        },
    )

    registry = WebhookRegistry(resource_root=tmp_path)

    with pytest.raises(ValueError, match="Could not find webhook schema"):
        registry.validate_webhook(
            event_name="meeting.started",
            payload={"event": "meeting.started"},
        )
