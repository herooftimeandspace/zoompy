

"""Contract tests for the Zoom Webinars Plus & Events (Zoom Events) REST API.

These tests are *implementation-agnostic* as long as your client exposes a small,
consistent surface area:

- `client.events.list_events(...)` (preferred)
  OR `client.events.get_events(...)`

And your client must delegate the HTTP call to one of the following internal
request methods (any one is fine):

- `client._request(method, path, *, params=None, json=None, data=None, headers=None)`
- `client.request(method, path, *, params=None, json=None, data=None, headers=None)`

If your client uses a different internal hook, either add a thin adapter or
rename your internal method. Humans love inventing 14 different names for the
same thing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, MutableMapping, Optional
from unittest.mock import MagicMock

import pytest
from jsonschema import Draft202012Validator


SCHEMA_PATH = Path(__file__).parent / "schemas" / "build_platform" / "Events.json"


def _load_openapi() -> dict:
    with SCHEMA_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _get_operation(openapi: dict, path: str, method: str) -> dict:
    paths = openapi.get("paths") or {}
    assert path in paths, f"OpenAPI schema missing path: {path}"

    op = (paths[path] or {}).get(method.lower())
    assert op is not None, f"OpenAPI schema missing operation: {method.upper()} {path}"
    return op


def _get_response_schema(openapi: dict, path: str, method: str, status: str = "200") -> dict:
    op = _get_operation(openapi, path, method)
    responses = op.get("responses") or {}
    assert status in responses, f"OpenAPI operation missing {status} response: {method.upper()} {path}"

    content = (responses[status] or {}).get("content") or {}
    app_json = content.get("application/json") or {}
    schema = app_json.get("schema")
    assert schema, f"OpenAPI {status} response missing application/json schema: {method.upper()} {path}"
    return schema


def _compile_validator(schema: dict) -> Draft202012Validator:
    # Most Zoom OpenAPI files embed JSON Schema inline and don't rely heavily on $ref.
    # If that changes later, we can add a ref-resolver, but let's not summon that misery yet.
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _find_request_hook(client: Any) -> Callable[..., Any]:
    for name in ("_request", "request"):
        hook = getattr(client, name, None)
        if callable(hook):
            return hook
    raise AssertionError(
        "Client must expose an internal request hook named `_request` or `request` "
        "so contract tests can verify HTTP method/path/params."
    )


def _find_events_api(client: Any) -> Any:
    events_api = getattr(client, "events", None)
    assert events_api is not None, "Client must expose an `events` API group (client.events)."
    return events_api


def _find_list_method(events_api: Any) -> Callable[..., Any]:
    for name in ("list_events", "get_events"):
        fn = getattr(events_api, name, None)
        if callable(fn):
            return fn
    raise AssertionError("Events API group must expose `list_events` (preferred) or `get_events`.")


@pytest.fixture(scope="session")
def events_openapi() -> dict:
    return _load_openapi()


@pytest.fixture(scope="session")
def list_events_response_validator(events_openapi: dict) -> Draft202012Validator:
    schema = _get_response_schema(events_openapi, "/zoom_events/events", "get", status="200")
    return _compile_validator(schema)


@pytest.fixture
def client():
    """Provide the API client under test.

    The project should provide this fixture in `src/tests/conftest.py`.
    We define a placeholder here so pytest shows a clear error message if it's missing.
    """

    raise RuntimeError(
        "Missing `client` fixture. Define one in src/tests/conftest.py that returns your Zoom API client instance."
    )


def _sample_list_events_payload() -> dict:
    # Built from the documented fields in the Events pagination schema.
    # The OpenAPI snippet shows these example shapes and typical values.
    # Keep it small but valid.
    return {
        "total_records": 1,
        "next_page_token": "",
        "events": [
            {
                "event_id": "234kj2h34kljgh23lkhj3",
                "name": "OpenAPI Conference Name",
                "description": "This event was created with the OpenAPI",
                "timezone": "America/Indianapolis",
                "event_type": "CONFERENCE",
            }
        ],
    }


class TestEventsListEventsContract:
    """Contract tests for GET /zoom_events/events (operationId: getEvents)."""

    def test_exposes_events_group(self, client: Any) -> None:
        _find_events_api(client)

    def test_exposes_list_method(self, client: Any) -> None:
        events_api = _find_events_api(client)
        _find_list_method(events_api)

    def test_list_events_calls_expected_http(self, client: Any) -> None:
        events_api = _find_events_api(client)
        list_events = _find_list_method(events_api)

        hook = _find_request_hook(client)
        # Patch the hook so we can inspect how the public method calls it.
        mock = MagicMock(return_value=_sample_list_events_payload())
        if hook.__name__ == "_request":
            client._request = mock  # type: ignore[attr-defined]
        else:
            client.request = mock  # type: ignore[attr-defined]

        result = list_events(
            role_type="host",
            event_status_type="upcoming",
            page_size=30,
            next_page_token="IAfJX3jsOLW7w3dokmFl84zOa0MAVGyMEB2",
        )

        assert result is not None
        mock.assert_called_once()

        args, kwargs = mock.call_args
        assert args[0].upper() == "GET"
        assert args[1] == "/zoom_events/events"

        params = kwargs.get("params") or {}
        assert params["role_type"] == "host"
        assert params["event_status_type"] == "upcoming"
        assert params["page_size"] == 30
        assert params["next_page_token"] == "IAfJX3jsOLW7w3dokmFl84zOa0MAVGyMEB2"

    def test_list_events_omits_none_query_params(self, client: Any) -> None:
        events_api = _find_events_api(client)
        list_events = _find_list_method(events_api)

        hook = _find_request_hook(client)
        mock = MagicMock(return_value=_sample_list_events_payload())
        if hook.__name__ == "_request":
            client._request = mock  # type: ignore[attr-defined]
        else:
            client.request = mock  # type: ignore[attr-defined]

        list_events(role_type=None, event_status_type=None, page_size=None, next_page_token=None)

        _, kwargs = mock.call_args
        params = kwargs.get("params") or {}
        assert "role_type" not in params
        assert "event_status_type" not in params
        assert "page_size" not in params
        assert "next_page_token" not in params

    @pytest.mark.parametrize(
        "role_type,event_status_type",
        [
            ("host", "upcoming"),
            ("host", "past"),
            ("host", "draft"),
            ("host", "cancelled"),
            ("attendee", "upcoming"),
            ("attendee", "past"),
        ],
    )
    def test_list_events_accepts_documented_enums(
        self, client: Any, role_type: str, event_status_type: str
    ) -> None:
        """The OpenAPI schema documents valid enums for role_type and event_status_type.

        Your client can validate these proactively (nice) or pass them through and let the API reject them.
        But it must not mutate them into something else.
        """

        events_api = _find_events_api(client)
        list_events = _find_list_method(events_api)

        hook = _find_request_hook(client)
        mock = MagicMock(return_value=_sample_list_events_payload())
        if hook.__name__ == "_request":
            client._request = mock  # type: ignore[attr-defined]
        else:
            client.request = mock  # type: ignore[attr-defined]

        list_events(role_type=role_type, event_status_type=event_status_type)

        _, kwargs = mock.call_args
        params = kwargs.get("params") or {}
        assert params.get("role_type") == role_type
        assert params.get("event_status_type") == event_status_type

    def test_list_events_response_validates_against_schema(
        self,
        client: Any,
        list_events_response_validator: Draft202012Validator,
    ) -> None:
        events_api = _find_events_api(client)
        list_events = _find_list_method(events_api)

        hook = _find_request_hook(client)
        payload = _sample_list_events_payload()
        mock = MagicMock(return_value=payload)
        if hook.__name__ == "_request":
            client._request = mock  # type: ignore[attr-defined]
        else:
            client.request = mock  # type: ignore[attr-defined]

        result = list_events(role_type="host", event_status_type="upcoming")

        errors = sorted(list_events_response_validator.iter_errors(result), key=lambda e: e.path)
        assert errors == [], "\n".join(str(e) for e in errors)

    def test_list_events_raises_on_http_error(self, client: Any) -> None:
        """Any sane client should raise on non-2xx responses.

        We don't care about the exact exception type, just that it isn't silently swallowed.
        """

        events_api = _find_events_api(client)
        list_events = _find_list_method(events_api)

        hook = _find_request_hook(client)

        def boom(*args: Any, **kwargs: Any) -> Any:
            raise Exception("HTTP 401")

        if hook.__name__ == "_request":
            client._request = boom  # type: ignore[attr-defined]
        else:
            client.request = boom  # type: ignore[attr-defined]

        with pytest.raises(Exception):
            list_events(role_type="host")