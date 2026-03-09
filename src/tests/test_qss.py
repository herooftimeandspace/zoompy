

"""Contract tests for Zoom QSS (Quality of Service Subscription) endpoints.

Schema location (repo layout):
    src/tests/schemas/accounts/qss.json

These tests validate that any client implementation:
- Calls the correct REST paths/methods.
- Passes through query params as defined by the schema.
- Returns JSON payloads that conform to the OpenAPI response schemas.
- Raises on non-2xx responses.

Yes, this is more strict than your average API client. That's the point.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import json

import pytest
from jsonschema import validate
from jsonschema.exceptions import ValidationError


SCHEMA_PATH = Path(__file__).parent / "schemas" / "accounts" / "qss.json"
DEFAULT_BASE_URL = "https://api.zoom.us/v2"


# ----------------------------
# Helpers: schema extraction
# ----------------------------


def _load_openapi() -> Dict[str, Any]:
    if not SCHEMA_PATH.exists():
        raise RuntimeError(
            f"Schema file not found at {SCHEMA_PATH}. "
            "Expected src/tests/schemas/accounts/qss.json"
        )
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _schema_for(path: str, method: str, status: str) -> Dict[str, Any]:
    doc = _load_openapi()
    paths = doc.get("paths", doc)
    op = paths[path][method.lower()]

    resp = op["responses"][status]
    content = resp.get("content")
    if not content:
        return {"type": "null"}
    return content["application/json"]["schema"]


# ----------------------------
# Fake HTTP layer (fast unit tests)
# ----------------------------


@dataclass
class FakeResponse:
    status_code: int
    payload: Any = None

    def json(self) -> Any:
        return self.payload


class FakeHTTPClient:
    """Minimal http client interface for contract testing.

    Your ZoomClient should accept an injected transport compatible with:
        http_client.request(method, url, params=..., json=..., headers=...)

    We record calls so tests can assert exact URLs/params.
    """

    def __init__(self) -> None:
        self.calls: List[Tuple[str, str, Dict[str, Any], Any]] = []
        self._queue: List[FakeResponse] = []

    def queue(self, *responses: FakeResponse) -> None:
        self._queue.extend(responses)

    def request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        json: Any = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> FakeResponse:
        self.calls.append((method.upper(), url, params or {}, json))
        if not self._queue:
            raise AssertionError("FakeHTTPClient has no queued responses")
        return self._queue.pop(0)


# ----------------------------
# Fixtures
# ----------------------------


@pytest.fixture()
def http() -> FakeHTTPClient:
    return FakeHTTPClient()


@pytest.fixture()
def client(http: FakeHTTPClient):
    """Client under test.

    Contract:
      - ZoomClient(token=..., base_url=..., http_client=...)
      - Exposes `client.qss` with methods:
          - list_meeting_participants_qos_summary(meeting_id, page_size=None, next_page_token=None)
          - list_webinar_participants_qos_summary(webinar_id, page_size=None, next_page_token=None)
          - list_videosdk_session_users_qos_summary(session_id, page_size=None, next_page_token=None)

    If you want different method names, that's cute. Make the implementation match the tests.
    """

    try:
        from zoompy.client import ZoomClient  # type: ignore
    except Exception:
        try:
            from zoompy import ZoomClient  # type: ignore
        except Exception as e:
            raise AssertionError(
                "Could not import ZoomClient. Expected `zoompy.client.ZoomClient` or `zoompy.ZoomClient`."
            ) from e

    return ZoomClient(token="test-token", base_url=DEFAULT_BASE_URL, http_client=http)


# ----------------------------
# Minimal valid payload factories
# ----------------------------


def _sample_participants_qos_summary_list() -> Dict[str, Any]:
    # Keep it minimal: only fields that are very commonly present.
    # The schema is permissive and mostly optional, but the pagination object usually exists.
    return {
        "page_size": 30,
        "next_page_token": "",
        "participants": [
            {
                "id": "zJKyaiAyTNC-MWjiWC18KQ",
                "participant_id": "20161536",
                "user_name": "someone",
                "email": "user@example.com",
                "qos": [
                    {
                        "type": "audio_input",
                        "details": {
                            "min_bitrate": "10kbps",
                            "avg_bitrate": "20kbps",
                            "max_bitrate": "30kbps",
                        },
                    }
                ],
            }
        ],
    }


def _sample_session_users_qos_summary_list() -> Dict[str, Any]:
    # Video SDK session users QoS Summary payload shape differs from meetings/webinars.
    # We'll still keep it small and schema-friendly.
    return {
        "page_size": 30,
        "next_page_token": "",
        "users": [
            {
                "id": "user_1",
                "name": "SDK User",
                "qos": [
                    {
                        "type": "video_output",
                        "details": {
                            "min_bitrate": "100kbps",
                            "avg_bitrate": "200kbps",
                            "max_bitrate": "400kbps",
                        },
                    }
                ],
            }
        ],
    }


# ----------------------------
# Tests: Meeting participants QoS Summary
# ----------------------------


def test_list_meeting_participants_qos_summary_calls_expected_endpoint(client, http: FakeHTTPClient):
    meeting_id = "4444AAAiAAAAAiAiAiiAii=="
    payload = _sample_participants_qos_summary_list()
    http.queue(FakeResponse(200, payload))

    result = client.qss.list_meeting_participants_qos_summary(
        meeting_id, page_size=10, next_page_token="tok"
    )

    assert http.calls, "Expected at least one HTTP call"
    method, url, params, body = http.calls[0]

    assert method == "GET"
    assert url == f"{DEFAULT_BASE_URL}/metrics/meetings/{meeting_id}/participants/qos_summary"
    assert body is None
    assert params.get("page_size") == 10
    assert params.get("next_page_token") == "tok"

    schema = _schema_for("/metrics/meetings/{meetingId}/participants/qos_summary", "get", "200")
    validate(instance=result, schema=schema)


# ----------------------------
# Tests: Webinar participants QoS Summary
# ----------------------------


def test_list_webinar_participants_qos_summary_calls_expected_endpoint(client, http: FakeHTTPClient):
    webinar_id = "dx0bdThTSkWQHS2a2QL2Ig=="
    payload = _sample_participants_qos_summary_list()
    http.queue(FakeResponse(200, payload))

    result = client.qss.list_webinar_participants_qos_summary(
        webinar_id, page_size=10, next_page_token="tok"
    )

    method, url, params, body = http.calls[0]

    assert method == "GET"
    assert url == f"{DEFAULT_BASE_URL}/metrics/webinars/{webinar_id}/participants/qos_summary"
    assert body is None
    assert params.get("page_size") == 10
    assert params.get("next_page_token") == "tok"

    schema = _schema_for("/metrics/webinars/{webinarId}/participants/qos_summary", "get", "200")
    validate(instance=result, schema=schema)


# ----------------------------
# Tests: Video SDK session users QoS Summary
# ----------------------------


def test_list_videosdk_session_users_qos_summary_calls_expected_endpoint(client, http: FakeHTTPClient):
    session_id = "sess_123"
    payload = _sample_session_users_qos_summary_list()
    http.queue(FakeResponse(200, payload))

    result = client.qss.list_videosdk_session_users_qos_summary(
        session_id, page_size=10, next_page_token="tok"
    )

    method, url, params, body = http.calls[0]

    assert method == "GET"
    assert url == f"{DEFAULT_BASE_URL}/videosdk/sessions/{session_id}/users/qos_summary"
    assert body is None
    assert params.get("page_size") == 10
    assert params.get("next_page_token") == "tok"

    schema = _schema_for("/videosdk/sessions/{sessionId}/users/qos_summary", "get", "200")
    validate(instance=result, schema=schema)


# ----------------------------
# Schema sanity checks
# ----------------------------


def test_meeting_qos_schema_rejects_garbage():
    schema = _schema_for("/metrics/meetings/{meetingId}/participants/qos_summary", "get", "200")
    with pytest.raises(ValidationError):
        validate(instance={"lol": "nope"}, schema=schema)


# ----------------------------
# Error handling contract
# ----------------------------


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 429, 500])
def test_qss_methods_raise_on_http_errors(client, http: FakeHTTPClient, status_code: int):
    http.queue(FakeResponse(status_code, {"message": "nope"}))

    with pytest.raises(Exception):
        client.qss.list_meeting_participants_qos_summary("4444", page_size=10)