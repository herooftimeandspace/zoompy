"""Curated SDK checks against the real bundled Zoom schema corpus.

The focused SDK tests in `test_sdk.py` keep behavior isolated with a tiny
temporary schema tree. This module complements them by sampling the actual
bundled OpenAPI documents that ship with `zoompy`.

These tests are intentionally small and opinionated. Their job is to catch
obvious regressions in namespace generation, typed-model availability, and
interactive discoverability across the real Zoom schema inventory.
"""

from __future__ import annotations

from pydantic import BaseModel

from zoompy import ZoomClient


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
