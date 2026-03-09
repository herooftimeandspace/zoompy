

"""Contract tests for the Zoom Cobrowse SDK endpoints.

Schema location (repo layout):
    src/tests/schemas/build_platform/Cobrowse SDK.json

These are contract tests: they define what your client *must* do.

Integration hook
----------------
Provide a `make_client` fixture in your project (usually in `src/tests/conftest.py`) with this shape:

    def make_client(transport: object):
        # Return a Zoom API client wired to use `transport` for HTTP.
        ...

The transport object must provide:

    request(method: str, url: str, *, headers=None, params=None, json=None) -> Response

Where `Response` has:

    status_code: int
    json() -> dict | None

Your client is expected to expose a `.cobrowse_sdk` (or `.cobrowse`) service. If your
implementation uses different names, adapt in the fixture.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import json

import pytest
from jsonschema import Draft202012Validator


SCHEMA_PATH = (
    Path(__file__).parent / "schemas" / "build_platform" / "Cobrowse SDK.json"
)


# -----------------------------
# Minimal test doubles
# -----------------------------


@dataclass
class FakeResponse:
    status_code: int
    _payload: Optional[dict] = None

    def json(self) -> Optional[dict]:
        return self._payload


class CapturingTransport:
    """A tiny HTTP transport spy.

    Your client should call `transport.request(...)`.
    The tests assert on captured method/url/headers/params/body.
    """

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._queue: List[FakeResponse] = []

    def enqueue(self, response: FakeResponse) -> None:
        self._queue.append(response)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json: Any = None,
    ) -> FakeResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "params": params or {},
                "json": json,
            }
        )
        if not self._queue:
            raise AssertionError(
                "Transport queue empty. Enqueue a FakeResponse before calling the client."
            )
        return self._queue.pop(0)


# -----------------------------
# Schema helpers
# -----------------------------


@pytest.fixture(scope="session")
def cobrowse_spec() -> dict:
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(
            f"Cobrowse SDK schema not found at {SCHEMA_PATH}. "
            "Expected src/tests/schemas/build_platform/Cobrowse SDK.json"
        )
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _response_schema(spec: dict, *, path: str, method: str, status: str) -> dict:
    op = spec["paths"][path][method]
    resp = op["responses"][status]
    content = resp.get("content") or {}
    if "application/json" not in content:
        raise KeyError(
            f"No application/json schema for {method.upper()} {path} {status}. "
            f"Available content types: {', '.join(content.keys()) if content else '<none>'}"
        )
    return content["application/json"]["schema"]


def _validate(instance: Any, schema: dict) -> None:
    Draft202012Validator(schema).validate(instance)


def _assert_call(
    transport: CapturingTransport,
    *,
    method: str,
    path_suffix: str,
    params: Optional[Dict[str, Any]] = None,
) -> None:
    assert transport.calls, "No HTTP calls captured. Your client didn't hit the transport."
    call = transport.calls[-1]

    assert call["method"].upper() == method.upper()
    assert call["url"].endswith(path_suffix)

    if params is not None:
        assert call["params"] == params


# -----------------------------
# Required integration hook
# -----------------------------


@pytest.fixture
def make_client() -> Callable[[Any], Any]:
    raise RuntimeError(
        "Provide a `make_client(transport)` fixture (e.g., in src/tests/conftest.py) "
        "that returns your Zoom client wired to use the provided transport."
    )


def _svc(client: Any) -> Any:
    """Locate the cobrowse service.

    Contract preference: `client.cobrowse_sdk`.
    Fallbacks: `client.cobrowse`.
    """

    if hasattr(client, "cobrowse_sdk"):
        return client.cobrowse_sdk
    if hasattr(client, "cobrowse"):
        return client.cobrowse
    raise AssertionError("Client must expose a Cobrowse service at .cobrowse_sdk or .cobrowse")


# -----------------------------
# Sample payloads (minimal but schema-valid)
# -----------------------------


def sample_live_sessions() -> dict:
    return {
        "page_size": 30,
        "next_page_token": "",
        "sessions": [
            {
                "session_id": "GDDykmpQQU6MWL3GqLNUkw",
                "start_time": "2025-02-14T19:09:01Z",
                "session_pin": "987536",
                "users": [
                    {
                        "user_id": "YZ8uRj9zRf2yY5cshmzrTA",
                        "user_name": "exampleuser",
                        "role_type": "agent",
                    }
                ],
            }
        ],
    }


def sample_past_sessions() -> dict:
    return {
        "from": "2025-02-14",
        "to": "2025-02-14",
        "page_size": 30,
        "next_page_token": "",
        "sessions": [
            {
                "session_id": "GDDykmpQQU6MWL3GqLNUkw",
                "start_time": "2025-02-14T19:09:01Z",
                "end_time": "2025-02-14T19:15:01Z",
                "duration": "00:03:19",
                "user_count": 2,
                "session_pin": "987536",
                "users": [
                    {
                        "user_id": "YZ8uRj9zRf2yY5cshmzrTA",
                        "user_name": "exampleuser",
                        "role_type": "agent",
                    }
                ],
            }
        ],
    }


def sample_session_details_live() -> dict:
    return {
        "session_id": "GDDykmpQQU6MWL3GqLNUkw",
        "start_time": "2025-02-14T19:09:01Z",
        "session_pin": "987536",
        "user_count": 2,
    }


def sample_session_details_past() -> dict:
    return {
        "session_id": "GDDykmpQQU6MWL3GqLNUkw",
        "start_time": "2025-02-14T19:09:01Z",
        "end_time": "2025-02-14T19:15:01Z",
        "duration": "00:03:19",
        "session_pin": "987536",
        "user_count": 2,
    }


def sample_session_users() -> dict:
    return {
        "page_size": 30,
        "next_page_token": "",
        "users": [
            {
                "user_connection_id": "Nlg8wgA0TFe3jT87CjQZqg",
                "user_id": "YZ8uRj9zRf2yY5cshmzrTA",
                "user_name": "exampleuser",
                "role_type": "agent",
                "ip_address": "127.0.0.1",
                "data_center": "US",
                "join_time": "2025-03-18T05:07:13Z",
                "leave_time": "2025-03-18T05:09:13Z",
                "duration": "00:03:19",
            }
        ],
    }


# -----------------------------
# Tests: /cobrowsesdk/live_sessions
# -----------------------------


def test_list_live_sessions_builds_correct_request_and_validates_response(
    cobrowse_spec: dict, make_client: Callable[[Any], Any]
) -> None:
    transport = CapturingTransport()
    client = make_client(transport)

    payload = sample_live_sessions()
    transport.enqueue(FakeResponse(200, payload))

    svc = _svc(client)
    result = svc.list_live_sessions(page_size=50, next_page_token="tok", session_pin="987536")

    _assert_call(
        transport,
        method="GET",
        path_suffix="/cobrowsesdk/live_sessions",
        params={"page_size": 50, "next_page_token": "tok", "session_pin": "987536"},
    )

    schema = _response_schema(
        cobrowse_spec, path="/cobrowsesdk/live_sessions", method="get", status="200"
    )
    _validate(result, schema)


# -----------------------------
# Tests: /cobrowsesdk/past_sessions
# -----------------------------


def test_list_past_sessions_builds_correct_request_and_validates_response(
    cobrowse_spec: dict, make_client: Callable[[Any], Any]
) -> None:
    transport = CapturingTransport()
    client = make_client(transport)

    payload = sample_past_sessions()
    transport.enqueue(FakeResponse(200, payload))

    svc = _svc(client)
    result = svc.list_past_sessions(
        time_type="start_time",
        from_="2025-02-14",
        to="2025-02-14",
        page_size=30,
        next_page_token="tok",
        session_id="GDDykmpQQU6MWL3GqLNUkw",
        session_pin="987536",
    )

    _assert_call(
        transport,
        method="GET",
        path_suffix="/cobrowsesdk/past_sessions",
        params={
            "time_type": "start_time",
            "from": "2025-02-14",
            "to": "2025-02-14",
            "page_size": 30,
            "next_page_token": "tok",
            "session_id": "GDDykmpQQU6MWL3GqLNUkw",
            "session_pin": "987536",
        },
    )

    schema = _response_schema(
        cobrowse_spec, path="/cobrowsesdk/past_sessions", method="get", status="200"
    )
    _validate(result, schema)


# -----------------------------
# Tests: /cobrowsesdk/sessions/{sessionId}
# -----------------------------


def test_get_session_details_live_validates_oneof_schema(
    cobrowse_spec: dict, make_client: Callable[[Any], Any]
) -> None:
    transport = CapturingTransport()
    client = make_client(transport)

    session_id = "GDDykmpQQU6MWL3GqLNUkw"
    payload = sample_session_details_live()
    transport.enqueue(FakeResponse(200, payload))

    svc = _svc(client)
    result = svc.get_session_details(session_id, session_type="live")

    _assert_call(
        transport,
        method="GET",
        path_suffix=f"/cobrowsesdk/sessions/{session_id}",
        params={"session_type": "live"},
    )

    schema = _response_schema(
        cobrowse_spec, path="/cobrowsesdk/sessions/{sessionId}", method="get", status="200"
    )
    _validate(result, schema)


def test_get_session_details_past_validates_oneof_schema(
    cobrowse_spec: dict, make_client: Callable[[Any], Any]
) -> None:
    transport = CapturingTransport()
    client = make_client(transport)

    session_id = "GDDykmpQQU6MWL3GqLNUkw"
    payload = sample_session_details_past()
    transport.enqueue(FakeResponse(200, payload))

    svc = _svc(client)
    result = svc.get_session_details(session_id, session_type="past")

    _assert_call(
        transport,
        method="GET",
        path_suffix=f"/cobrowsesdk/sessions/{session_id}",
        params={"session_type": "past"},
    )

    schema = _response_schema(
        cobrowse_spec, path="/cobrowsesdk/sessions/{sessionId}", method="get", status="200"
    )
    _validate(result, schema)


# -----------------------------
# Tests: /cobrowsesdk/sessions/{sessionId}/users
# -----------------------------


def test_list_session_users_builds_correct_request_and_validates_response(
    cobrowse_spec: dict, make_client: Callable[[Any], Any]
) -> None:
    transport = CapturingTransport()
    client = make_client(transport)

    session_id = "GDDykmpQQU6MWL3GqLNUkw"
    payload = sample_session_users()
    transport.enqueue(FakeResponse(200, payload))

    svc = _svc(client)
    result = svc.list_session_users(session_id, page_size=30, next_page_token="tok")

    _assert_call(
        transport,
        method="GET",
        path_suffix=f"/cobrowsesdk/sessions/{session_id}/users",
        params={"page_size": 30, "next_page_token": "tok"},
    )

    schema = _response_schema(
        cobrowse_spec,
        path="/cobrowsesdk/sessions/{sessionId}/users",
        method="get",
        status="200",
    )
    _validate(result, schema)


# -----------------------------
# Error handling (basic contract)
# -----------------------------


@pytest.mark.parametrize(
    "status_code",
    [400, 401, 404, 429],
)
def test_cobrowse_sdk_non_2xx_responses_raise_an_exception(
    make_client: Callable[[Any], Any], status_code: int
) -> None:
    transport = CapturingTransport()
    client = make_client(transport)

    # payload content doesn't matter; client must raise on non-2xx.
    transport.enqueue(FakeResponse(status_code, {"message": "nope"}))

    svc = _svc(client)
    with pytest.raises(Exception):
        svc.list_live_sessions()


# -----------------------------
# Bonus: spec sanity checks
# -----------------------------


def test_cobrowse_openapi_has_expected_paths(cobrowse_spec: dict) -> None:
    paths = cobrowse_spec.get("paths", {})
    assert "/cobrowsesdk/live_sessions" in paths
    assert "/cobrowsesdk/past_sessions" in paths
    assert "/cobrowsesdk/sessions/{sessionId}" in paths
    assert "/cobrowsesdk/sessions/{sessionId}/users" in paths
