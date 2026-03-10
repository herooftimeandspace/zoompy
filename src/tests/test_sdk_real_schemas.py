"""Curated golden SDK checks against the real bundled Zoom schema corpus.

The focused SDK tests in `test_sdk.py` keep behavior isolated with a tiny
temporary schema tree. This module complements them by sampling the actual
bundled OpenAPI documents that ship with `zoompy`.

These tests are intentionally opinionated. Their job is to pin the public SDK
surface that outside projects are expected to rely on, especially across the
largest and messiest endpoint families. When the dynamic SDK layer changes, we
want failures here to read like a contract diff rather than a mystery.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from zoompy import ZoomClient, __version__

_GOLDEN_PUBLIC_SURFACE_PATH = (
    Path(__file__).parent / "golden" / "sdk_public_surface.json"
)

_TOP_LEVEL_NAMESPACE_CHECKS = (
    "users.list",
    "users.get",
    "phone.users.get",
    "phone.users.update_profile",
    "phone.call_queues.list",
    "phone.devices.get",
    "rooms.get_profile",
    "rooms.list_rooms",
    "rooms.delete_room",
    "rooms.locations.list",
    "rooms.locations.get_profile",
    "whiteboard.get_whiteboard",
    "whiteboard.update_metadata",
    "whiteboard.projects.list",
    "meetings.update_meeting",
    "chat.channels.get_account",
)

_HELPER_METHOD_CHECKS = (
    "users.list.iter_pages",
    "users.list.iter_all",
    "users.list.paginate",
    "users.get.raw",
)

_STABLE_OPERATION_IDS = {
    "users.list": "users",
    "users.get": "user",
    "phone.users.get": "phoneUser",
    "phone.users.update_profile": "updateUserProfile",
    "phone.call_queues.list": "listCallQueues",
    "phone.call_queues.get": "getACallQueue",
    "phone.devices.list": "listPhoneDevices",
    "phone.devices.get": "getADevice",
    "meetings.meeting_summaries.list": "Listmeetingsummaries",
    "meetings.update_meeting": "meetingUpdate",
    "chat.channels.get": "getUserLevelChannel",
    "chat.channels.get_account": "getAccountChannels",
    "rooms.add_room": "addARoom",
    "rooms.delete_room": "deleteAZoomRoom",
    "rooms.get_profile": "getZRProfile",
    "rooms.list_rooms": "listZoomRooms",
    "rooms.update_profile": "updateRoomProfile",
    "rooms.locations.list": "listZRLocations",
    "rooms.locations.get_profile": "getZRLocationProfile",
    "rooms.locations.update_profile": "updateZRLocationProfile",
    "scheduler.schedules.get": "get_schedule",
    "whiteboard.get_whiteboard": "GetAWhiteboard",
    "whiteboard.delete_whiteboard": "DeleteAWhiteboard",
    "whiteboard.update_metadata": "UpdateAWhiteboardMetadata",
    "whiteboard.projects.list": "Listallprojects",
    "whiteboard.projects.get": "Getaproject",
    "whiteboard.projects.create": "Createproject",
}

_ALIAS_EQUIVALENTS = {
    "phone.devices.get": "phone.devices.get_device",
    "phone.call_queues.get": "phone.call_queues.get_call_queue",
    "chat.channels.get_account": "chat.channels.get_account_channels",
    "rooms.get_profile": "rooms.get_zr_profile",
    "rooms.locations.get_profile": "rooms.locations.get_zr_location_profile",
    "whiteboard.get_whiteboard": "whiteboard.get_a_whiteboard",
    "whiteboard.projects.get": "whiteboard.projects.getaproject",
}

_PREFERRED_ALIAS_PRESENCE = {
    "phone.users": ("get", "list", "update_profile"),
    "phone.devices": ("get", "list", "update", "delete", "create"),
    "chat.channels": (
        "get",
        "get_account",
        "delete_user_level",
        "update_user_level",
    ),
    "rooms": ("add_room", "delete_room", "get_profile", "list_rooms", "update_profile"),
    "whiteboard": ("get_whiteboard", "delete_whiteboard", "update_metadata"),
    "whiteboard.projects": ("get", "list", "create"),
}

_TYPED_MODEL_EXPECTATIONS = {
    "phone.users.get": {"response": True, "request": False},
    "phone.devices.get": {"response": True, "request": False},
    "phone.call_queues.get": {"response": True, "request": False},
    "chat.channels.get": {"response": True, "request": False},
    "chat.channels.get_account": {"response": True, "request": False},
    "rooms.get_profile": {"response": True, "request": False},
    "rooms.locations.get_profile": {"response": True, "request": False},
    "whiteboard.get_whiteboard": {"response": True, "request": False},
    "whiteboard.projects.get": {"response": True, "request": False},
    "whiteboard.projects.create": {"response": True, "request": True},
}

_SCHEMA_PARAMETER_NAMES = {
    "phone.users.get": {"path": ["user_id"], "query": []},
    "phone.users.update_profile": {"path": ["user_id"], "query": []},
    "phone.call_queues.get": {"path": ["call_queue_id"], "query": []},
    "phone.devices.get": {"path": ["device_id"], "query": []},
    "chat.channels.get_account": {
        "path": [],
        "query": ["page_size", "next_page_token"],
    },
    "chat.channels.delete_user_level": {
        "path": ["channel_id"],
        "query": [],
    },
    "rooms.get_profile": {
        "path": ["room_id"],
        "query": ["regenerate_activation_code"],
    },
    "rooms.locations.get_profile": {
        "path": ["location_id"],
        "query": [],
    },
    "whiteboard.get_whiteboard": {
        "path": ["whiteboard_id"],
        "query": [],
    },
    "whiteboard.projects.get": {
        "path": ["project_id"],
        "query": [],
    },
}


@pytest.fixture
def client() -> Iterator[ZoomClient]:
    """Create one schema-only client per test and close it reliably.

    These tests never hit the network. They only inspect the SDK surface that
    `zoompy` builds from packaged schemas, so an explicit access token is
    enough to bypass live OAuth.
    """

    sdk_client = ZoomClient(access_token="test-access-token")
    try:
        yield sdk_client
    finally:
        sdk_client.close()


def _resolve_member(root: Any, dotted_path: str) -> Any:
    """Resolve a dotted SDK path like `phone.devices.get` from the client.

    Keeping path resolution in one helper makes the test data much easier to
    read. The golden constants above can stay focused on the public contract
    instead of repeating nested attribute access everywhere.
    """

    member = root
    for part in dotted_path.split("."):
        member = getattr(member, part)
    return member


def _collect_operation_ids(client: ZoomClient, paths: tuple[str, ...]) -> dict[str, str]:
    """Map dotted SDK method paths to their underlying OpenAPI operation IDs."""

    return {
        path: _resolve_member(client, path)._operation.operation_id
        for path in paths
    }


def _collect_preferred_aliases(
    client: ZoomClient, expectations: dict[str, tuple[str, ...]]
) -> dict[str, dict[str, bool]]:
    """Record whether each preferred alias exists on the target namespace."""

    return {
        namespace: {
            alias: hasattr(_resolve_member(client, namespace), alias)
            for alias in aliases
        }
        for namespace, aliases in expectations.items()
    }


def _collect_model_flags(
    client: ZoomClient, expectations: dict[str, dict[str, bool]]
) -> dict[str, dict[str, bool]]:
    """Collect request/response model availability for selected methods."""

    flags: dict[str, dict[str, bool]] = {}
    for path in expectations:
        method = _resolve_member(client, path)
        flags[path] = {
            "response": method.response_model is not None,
            "request": method.request_model is not None,
        }
    return flags


def _collect_parameter_names(
    client: ZoomClient, expectations: dict[str, dict[str, list[str]]]
) -> dict[str, dict[str, list[str]]]:
    """Collect normalized snake_case path/query parameter names."""

    names: dict[str, dict[str, list[str]]] = {}
    for path in expectations:
        operation = _resolve_member(client, path)._operation
        names[path] = {
            "path": [
                parameter.python_name for parameter in operation.path_parameters
            ],
            "query": [
                parameter.python_name for parameter in operation.query_parameters
            ],
        }
    return names


def _collect_public_sdk_inventory(client: ZoomClient) -> dict[str, dict[str, Any]]:
    """Collect every generated SDK method from the internal service tree.

    `ServiceNode` also exposes helper methods such as `add_child` and
    `get_member`, which are part of the implementation rather than the public
    generated API surface. Walking the internal `_children` and `_methods`
    maps lets this test pin only the real schema-derived endpoint methods.
    """

    _ = client.sdk
    root = client._sdk._root

    def walk(node: Any, prefix: tuple[str, ...] = ()) -> dict[str, dict[str, Any]]:
        inventory: dict[str, dict[str, Any]] = {}
        for name, method in sorted(node._methods.items()):
            path = ".".join(prefix + (name,))
            inventory[path] = {
                "operation_id": method._operation.operation_id,
                "http_method": method._operation.http_method,
                "path": method._operation.path,
                "path_parameters": [
                    parameter.python_name
                    for parameter in method._operation.path_parameters
                ],
                "query_parameters": [
                    parameter.python_name
                    for parameter in method._operation.query_parameters
                ],
                "has_request_model": method.request_model is not None,
                "has_response_model": method.response_model is not None,
            }
        for name, child in sorted(node._children.items()):
            inventory.update(walk(child, prefix + (name,)))
        return inventory

    return walk(root)


def _load_golden_public_surface() -> dict[str, dict[str, Any]]:
    """Load the checked-in full SDK inventory used as the golden contract."""

    return json.loads(_GOLDEN_PUBLIC_SURFACE_PATH.read_text(encoding="utf-8"))


def test_real_schema_sdk_exposes_expected_top_level_namespaces(
    client: ZoomClient,
) -> None:
    """Expose the top-level SDK surface that scripts are expected to use."""

    assert "users" in dir(client)
    assert "phone" in dir(client)
    assert "meetings" in dir(client)

    for path in _TOP_LEVEL_NAMESPACE_CHECKS + _HELPER_METHOD_CHECKS:
        assert callable(_resolve_member(client, path))

    assert not hasattr(client.phone, "call_queue_analytic")


def test_real_schema_sdk_exposes_typed_models_for_common_operations(
    client: ZoomClient,
) -> None:
    """Build typed models from packaged schemas for representative methods."""

    get_user_model = client.users.get.response_model
    create_user_request_model = client.users.create.request_model
    get_phone_user_model = client.phone.users.get.response_model

    assert get_user_model is not None
    assert create_user_request_model is not None
    assert get_phone_user_model is not None

    assert issubclass(get_user_model, BaseModel)
    assert issubclass(create_user_request_model, BaseModel)
    assert issubclass(get_phone_user_model, BaseModel)


def test_real_schema_sdk_docstrings_include_operation_metadata(
    client: ZoomClient,
) -> None:
    """Keep generated SDK methods understandable in editors and shells."""

    docstring = client.phone.users.get.__doc__

    assert docstring is not None
    assert "Operation ID:" in docstring
    assert "HTTP:" in docstring
    assert "/phone/users/{userId}" in docstring


def test_real_schema_sdk_operation_ids_stay_stable(client: ZoomClient) -> None:
    """Pin the preferred public SDK methods to specific OpenAPI operations."""

    operation_ids = _collect_operation_ids(
        client, tuple(_STABLE_OPERATION_IDS.keys())
    )

    assert operation_ids == _STABLE_OPERATION_IDS


def test_real_schema_sdk_full_public_surface_matches_golden_inventory(
    client: ZoomClient,
) -> None:
    """Pin every generated SDK method against a checked-in golden manifest.

    The curated assertions in this file keep important families human-readable.
    This exhaustive inventory does the opposite job: it makes sure *any*
    refactor that changes the generated public SDK surface has to update a
    reviewable artifact on purpose.
    """

    assert _collect_public_sdk_inventory(client) == _load_golden_public_surface()


def test_real_schema_sdk_prefers_clean_aliases_for_noisy_families(
    client: ZoomClient,
) -> None:
    """Keep cleaner aliases mapped to the same operations as fallback names.

    The ugliest Zoom families still expose raw generated spellings such as
    `get_device` or `getaproject`. Those fallbacks are useful escape hatches,
    but the preferred aliases should remain the obvious public surface.
    """

    alias_pairs = {
        preferred: (
            _resolve_member(client, preferred)._operation.operation_id,
            _resolve_member(client, fallback)._operation.operation_id,
        )
        for preferred, fallback in _ALIAS_EQUIVALENTS.items()
    }

    assert alias_pairs == {
        preferred: (
            _STABLE_OPERATION_IDS[preferred],
            _STABLE_OPERATION_IDS[preferred],
        )
        for preferred in _ALIAS_EQUIVALENTS
    }


def test_real_schema_sdk_exposes_preferred_aliases_on_noisy_families(
    client: ZoomClient,
) -> None:
    """Keep the clean public aliases visible on the messiest namespaces."""

    assert _collect_preferred_aliases(client, _PREFERRED_ALIAS_PRESENCE) == {
        namespace: {alias: True for alias in aliases}
        for namespace, aliases in _PREFERRED_ALIAS_PRESENCE.items()
    }


def test_real_schema_sdk_exposes_typed_models_on_noisy_families(
    client: ZoomClient,
) -> None:
    """Pin typed-model availability on the ugliest real families.

    This keeps the dynamic SDK honest: the noisy parts of the Zoom API should
    still feel like a typed scripting SDK, not a thin JSON wrapper.
    """

    model_flags = _collect_model_flags(client, _TYPED_MODEL_EXPECTATIONS)

    assert model_flags == _TYPED_MODEL_EXPECTATIONS

    for path in _TYPED_MODEL_EXPECTATIONS:
        method = _resolve_member(client, path)
        if method.response_model is not None:
            assert issubclass(method.response_model, BaseModel)
        if method.request_model is not None:
            assert issubclass(method.request_model, BaseModel)


def test_real_schema_sdk_keeps_schema_derived_parameter_names(
    client: ZoomClient,
) -> None:
    """Require schema-derived snake_case parameter names on noisy methods.

    Generated SDK methods intentionally accept `**kwargs`, so the stable source
    of truth is the normalized operation metadata behind each method.
    """

    assert _collect_parameter_names(client, _SCHEMA_PARAMETER_NAMES) == (
        _SCHEMA_PARAMETER_NAMES
    )


def test_package_exposes_a_stable_version_string() -> None:
    """Expose an explicit package version for outside consumers to pin."""

    assert isinstance(__version__, str)
    assert __version__
