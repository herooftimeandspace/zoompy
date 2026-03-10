"""Focused production-side tests for runtime schema registries.

The broad contract suites prove the repository's schema-driven tests work, but
they do not directly prove that the production `zoompy` runtime classes load
the same schema families correctly. These tests stay narrow on purpose:

* one test proves `SchemaRegistry` indexes a master-account path
* one test proves `ZoomClient.validate_webhook()` uses the runtime webhook
  registry rather than only the test-only helpers
* one test proves ambiguous webhook lookups require extra disambiguation

That gives future maintainers a fast signal if the production schema layer
drifts away from the repository's sync and test structure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from zoompy import ZoomClient, WebhookRegistry
from zoompy.schema import SchemaRegistry


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write one small JSON document to a temporary test schema tree."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_schema_registry_loads_master_account_paths(tmp_path: Path) -> None:
    """Prove the production registry indexes master-account documents.

    This test uses a tiny temporary schema tree instead of depending on the
    repository having already synced a real `ma/master.json` document. The goal
    is to verify loader behavior, not Zoom's current download inventory.
    """

    _write_json(
        tmp_path / "master_accounts" / "accounts" / "Accounts.json",
        {
            "openapi": "3.0.0",
            "info": {"title": "Accounts"},
            "servers": [{"url": "https://api.zoom.us/v2"}],
            "paths": {
                "/accounts/{accountId}/lock_settings": {
                    "get": {
                        "operationId": "getMasterAccountLockSettings",
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "account_id": {"type": "string"}
                                            },
                                            "required": ["account_id"],
                                        }
                                    }
                                }
                            }
                        },
                    }
                }
            },
        },
    )

    registry = SchemaRegistry(resource_root=tmp_path)
    operation = registry.find_operation(
        method="GET",
        raw_path="/accounts/{accountId}/lock_settings",
        actual_path="/accounts/abc123/lock_settings",
    )

    assert operation.schema_name == "Accounts"
    assert operation.template_path == "/accounts/{accountId}/lock_settings"
    assert registry.base_url_for_request(
        method="GET",
        raw_path="/accounts/{accountId}/lock_settings",
        actual_path="/accounts/abc123/lock_settings",
        fallback="https://example.invalid",
    ) == "https://api.zoom.us/v2"


def test_zoom_client_validates_webhooks_with_runtime_registry(
    tmp_path: Path,
) -> None:
    """Prove webhook validation works through the production client API."""

    _write_json(
        tmp_path / "webhooks" / "workplace" / "Meetings.json",
        {
            "openapi": "3.1.0",
            "info": {"title": "Meetings"},
            "webhooks": {
                "meeting.started": {
                    "post": {
                        "operationId": "meetingStartedWebhook",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "event": {"type": "string"},
                                            "payload": {
                                                "type": "object",
                                                "properties": {
                                                    "object": {
                                                        "type": "object",
                                                        "properties": {
                                                            "id": {
                                                                "type": "string"
                                                            }
                                                        },
                                                        "required": ["id"],
                                                    }
                                                },
                                                "required": ["object"],
                                            },
                                        },
                                        "required": ["event", "payload"],
                                    }
                                }
                            }
                        },
                    }
                }
            },
        },
    )

    client = ZoomClient(
        access_token="test-access-token",
        webhook_registry=WebhookRegistry(resource_root=tmp_path),
    )
    try:
        client.validate_webhook(
            "meeting.started",
            {
                "event": "meeting.started",
                "payload": {"object": {"id": "12345"}},
            },
        )
    finally:
        client.close()


def test_webhook_registry_requires_disambiguation_for_duplicate_events(
    tmp_path: Path,
) -> None:
    """Require callers to narrow duplicate event names explicitly.

    The runtime webhook API accepts plain `event_name` lookups for convenience,
    but some event names could plausibly appear in more than one schema family.
    In that case we want a loud, predictable failure rather than silently
    picking one document and validating against the wrong contract.
    """

    shared_webhook = {
        "openapi": "3.1.0",
        "webhooks": {
            "meeting.started": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "event": {"type": "string"},
                                    },
                                    "required": ["event"],
                                }
                            }
                        }
                    }
                }
            }
        },
    }

    _write_json(
        tmp_path / "webhooks" / "workplace" / "Meetings.json",
        {
            **shared_webhook,
            "info": {"title": "Meetings"},
        },
    )
    _write_json(
        tmp_path / "webhooks" / "marketplace" / "Marketplace.json",
        {
            **shared_webhook,
            "info": {"title": "Marketplace"},
        },
    )

    registry = WebhookRegistry(resource_root=tmp_path)

    with pytest.raises(ValueError, match="ambiguous"):
        registry.validate_webhook(
            event_name="meeting.started",
            payload={"event": "meeting.started"},
        )
