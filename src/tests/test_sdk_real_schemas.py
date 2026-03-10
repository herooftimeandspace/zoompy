"""Curated golden SDK checks against the real bundled Zoom schema corpus.

The focused SDK tests in `test_sdk.py` keep behavior isolated with a tiny
temporary schema tree. This module complements them by sampling the actual
bundled OpenAPI documents that ship with `zoompy`.

These tests are intentionally opinionated. Their job is to pin the public SDK
surface that outside projects are expected to rely on, especially across the
largest endpoint families.
"""

from __future__ import annotations

from pydantic import BaseModel

from zoompy import ZoomClient, __version__


def _build_client() -> ZoomClient:
    """Create a client that can inspect bundled schemas without live auth."""

    return ZoomClient(access_token="test-access-token")


def test_real_schema_sdk_exposes_expected_top_level_namespaces() -> None:
    """Expose well-known endpoint families from the real packaged schemas."""

    client = _build_client()
    try:
        assert "users" in dir(client)
        assert "phone" in dir(client)
        assert "meetings" in dir(client)

        assert callable(client.users.list)
        assert callable(client.users.get)
        assert callable(client.phone.users.get)
        assert callable(client.phone.users.update_profile)
        assert callable(client.phone.call_queues.list)
        assert callable(client.phone.devices.get)
        assert callable(client.rooms.get_profile)
        assert callable(client.rooms.list_rooms)
        assert callable(client.rooms.delete_room)
        assert callable(client.rooms.locations.list)
        assert callable(client.rooms.locations.get_profile)
        assert callable(client.whiteboard.get_whiteboard)
        assert callable(client.whiteboard.update_metadata)
        assert callable(client.whiteboard.projects.list)
        assert callable(client.meetings.update_meeting)
        assert callable(client.chat.channels.get_account)
        assert callable(client.users.list.iter_pages)
        assert callable(client.users.list.iter_all)
        assert callable(client.users.list.paginate)
        assert callable(client.users.get.raw)
        assert not hasattr(client.phone, "call_queue_analytic")
    finally:
        client.close()


def test_real_schema_sdk_exposes_typed_models_for_common_operations() -> None:
    """Build request and response models from the real bundled schemas."""

    client = _build_client()
    try:
        get_user_model = client.users.get.response_model
        create_user_request_model = client.users.create.request_model
        get_phone_user_model = client.phone.users.get.response_model
    finally:
        client.close()

    assert get_user_model is not None
    assert create_user_request_model is not None
    assert get_phone_user_model is not None

    assert issubclass(get_user_model, BaseModel)
    assert issubclass(create_user_request_model, BaseModel)
    assert issubclass(get_phone_user_model, BaseModel)


def test_real_schema_sdk_docstrings_include_operation_metadata() -> None:
    """Keep generated SDK methods understandable in editors and shells."""

    client = _build_client()
    try:
        docstring = client.phone.users.get.__doc__
    finally:
        client.close()

    assert docstring is not None
    assert "Operation ID:" in docstring
    assert "HTTP:" in docstring
    assert "/phone/users/{userId}" in docstring


def test_real_schema_sdk_common_method_names_are_stable() -> None:
    """Pin a few important public method names as a golden SDK contract."""

    client = _build_client()
    try:
        assert client.users.list._operation.operation_id == "users"
        assert client.users.get._operation.operation_id == "user"
        assert client.phone.users.get._operation.operation_id == "phoneUser"
        assert client.phone.users.update_profile._operation.operation_id == (
            "updateUserProfile"
        )
        assert client.phone.devices.get._operation.operation_id == "getADevice"
        assert client.rooms.get_profile._operation.operation_id == "getZRProfile"
        assert client.rooms.locations.get_profile._operation.operation_id == (
            "getZRLocationProfile"
        )
        assert client.rooms.list_rooms._operation.operation_id == "listZoomRooms"
        assert client.chat.channels.get_account._operation.operation_id == (
            "getAccountChannels"
        )
        assert client.whiteboard.update_metadata._operation.operation_id == (
            "UpdateAWhiteboardMetadata"
        )
        assert client.whiteboard.projects.list._operation.operation_id == (
            "Listallprojects"
        )
    finally:
        client.close()


def test_real_schema_sdk_golden_matrix_for_major_families() -> None:
    """Pin a broader set of stable public SDK methods across major families."""

    client = _build_client()
    try:
        matrix = {
            "users.list": client.users.list._operation.operation_id,
            "users.get": client.users.get._operation.operation_id,
            "phone.users.get": client.phone.users.get._operation.operation_id,
            "phone.users.update_profile": (
                client.phone.users.update_profile._operation.operation_id
            ),
            "phone.call_queues.list": (
                client.phone.call_queues.list._operation.operation_id
            ),
            "phone.call_queues.get": (
                client.phone.call_queues.get._operation.operation_id
            ),
            "phone.devices.list": client.phone.devices.list._operation.operation_id,
            "phone.devices.get": client.phone.devices.get._operation.operation_id,
            "meetings.meeting_summaries.list": (
                client.meetings.meeting_summaries.list._operation.operation_id
            ),
            "meetings.update_meeting": (
                client.meetings.update_meeting._operation.operation_id
            ),
            "chat.channels.get": client.chat.channels.get._operation.operation_id,
            "chat.channels.get_account": (
                client.chat.channels.get_account._operation.operation_id
            ),
            "rooms.add_room": client.rooms.add_room._operation.operation_id,
            "rooms.delete_room": client.rooms.delete_room._operation.operation_id,
            "rooms.get_profile": client.rooms.get_profile._operation.operation_id,
            "rooms.list_rooms": client.rooms.list_rooms._operation.operation_id,
            "rooms.update_profile": client.rooms.update_profile._operation.operation_id,
            "rooms.locations.list": (
                client.rooms.locations.list._operation.operation_id
            ),
            "rooms.locations.get_profile": (
                client.rooms.locations.get_profile._operation.operation_id
            ),
            "rooms.locations.update_profile": (
                client.rooms.locations.update_profile._operation.operation_id
            ),
            "scheduler.schedules.get": (
                client.scheduler.schedules.get._operation.operation_id
            ),
            "whiteboard.get_whiteboard": (
                client.whiteboard.get_whiteboard._operation.operation_id
            ),
            "whiteboard.delete_whiteboard": (
                client.whiteboard.delete_whiteboard._operation.operation_id
            ),
            "whiteboard.update_metadata": (
                client.whiteboard.update_metadata._operation.operation_id
            ),
            "whiteboard.projects.list": (
                client.whiteboard.projects.list._operation.operation_id
            ),
            "whiteboard.projects.get": (
                client.whiteboard.projects.get._operation.operation_id
            ),
            "whiteboard.projects.create": (
                client.whiteboard.projects.create._operation.operation_id
            ),
        }
    finally:
        client.close()

    assert matrix == {
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


def test_package_exposes_a_stable_version_string() -> None:
    """Expose an explicit package version for outside consumers to pin."""

    assert isinstance(__version__, str)
    assert __version__
