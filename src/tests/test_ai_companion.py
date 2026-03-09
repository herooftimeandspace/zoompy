

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import httpx
import pytest
import respx
from jsonschema import Draft202012Validator


# Contract tests for the Zoom AI Companion API surface.
#
# Expected implementation shape (duck-typed):
# - `zoompy.client.ZoomClient` exists.
# - `ZoomClient(...).ai_companion` exists.
# - `ai_companion` exposes one method per OpenAPI `operationId` in snake_case.
# - Methods:
#     * perform the correct HTTP request (method + path)
#     * return decoded JSON (dict/list)
#     * validate decoded JSON against the OpenAPI response schema
#     * raise ValueError on schema mismatch


SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "workplace" / "AI Companion.json"
BASE_URL = "https://api.zoom.us/v2"


@dataclass(frozen=True)
class OperationCase:
    operation_id: str
    method: str
    path_template: str
    status_code: int


AIC_CASES: tuple[OperationCase, ...] = (
    OperationCase(
        operation_id="GetAICconversationarchives",
        method="GET",
        path_template="/aic/users/{userId}/conversation_archive",
        status_code=200,
    ),
)


def _snake_case(name: str) -> str:
    out: list[str] = []
    for ch in name:
        if ch.isupper() and out:
            out.append("_")
        out.append(ch.lower())
    return "".join(out).replace("__", "_")


def _load_openapi() -> dict[str, Any]:
    if not SCHEMA_PATH.exists():
        raise AssertionError(f"Missing schema file: {SCHEMA_PATH}")
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _get_operation(spec: Mapping[str, Any], path: str, method: str) -> Mapping[str, Any]:
    paths = spec.get("paths")
    assert isinstance(paths, Mapping), "OpenAPI spec must have a paths object"
    item = paths.get(path)
    assert isinstance(item, Mapping), f"OpenAPI spec missing path: {path}"
    op = item.get(method.lower())
    assert isinstance(op, Mapping), f"OpenAPI spec missing operation: {method.upper()} {path}"
    return op


def _get_json_response_schema(
    spec: Mapping[str, Any], path: str, method: str, status_code: int
) -> Mapping[str, Any]:
    op = _get_operation(spec, path, method)
    responses = op.get("responses")
    assert isinstance(responses, Mapping), "Operation.responses must be an object"
    resp = responses.get(str(status_code))
    assert isinstance(resp, Mapping), f"Missing documented {status_code} response"
    content = resp.get("content")
    assert isinstance(content, Mapping), f"{status_code} response must define content"
    app_json = content.get("application/json")
    assert isinstance(app_json, Mapping), f"{status_code} response must define application/json"
    schema = app_json.get("schema")
    assert isinstance(schema, Mapping), f"{status_code} response must define a JSON schema"
    return schema


def _render_path(path_template: str, **params: str) -> str:
    out = path_template
    for k, v in params.items():
        out = out.replace("{" + k + "}", v)
    return out


def _minimal_conversation_archive_response() -> dict[str, Any]:
    # Taken from the schema examples; intentionally minimal but schema-valid.
    return {
        "user_id": "ABCDEF123456",
        "email": "jchill@example.com",
        "display_name": "Jill Chill",
        "start_time": "2021-04-26T05:23:18Z",
        "end_time": "2021-05-26T05:23:18Z",
        "timezone": "Asia/Shanghai",
        "aic_history_download_url": "https://aic.zoom.us/rest/v1/aic/archive/conversations/download/Qg75t7xZBtEbAkjdlgbfdngBBBB",
        "file_extension": "JSON",
        "file_size": 165743,
        "file_type": "AIC_CONVERSATION",
        "physical_files": [
            {
                "file_id": "pvKocCqVSMygaOcKus5Afw",
                "file_name": "Screenshot 2025-02-12 at 10.42.27 AM.png",
                "file_size": 540680,
                "download_url": "https://aic.zoom.us/rest/v1/aic/archive/attached/download/HBAXbHc15BXbnq0JoDu6tc5MWlww9MAo9JJq2d14VAWkpcT5FEA.AK5calud4EJB7bMq",
            }
        ],
    }


@pytest.fixture(scope="module")
def aic_spec() -> dict[str, Any]:
    return _load_openapi()


def test_ai_companion_schema_sanity(aic_spec: Mapping[str, Any]) -> None:
    assert aic_spec.get("openapi") == "3.0.0"
    info = aic_spec.get("info")
    assert isinstance(info, Mapping)
    assert info.get("title") == "AI Companion"

    servers = aic_spec.get("servers")
    assert isinstance(servers, list) and servers
    assert any(isinstance(s, Mapping) and s.get("url") == BASE_URL for s in servers)

    # Ensure the documented path exists.
    _get_operation(aic_spec, "/aic/users/{userId}/conversation_archive", "GET")


def test_ai_companion_schema_embedded_json_schema_validates(aic_spec: Mapping[str, Any]) -> None:
    schema = _get_json_response_schema(
        aic_spec, "/aic/users/{userId}/conversation_archive", "GET", 200
    )
    Draft202012Validator.check_schema(schema)


def test_ai_companion_client_surface_area_matches_operation_ids() -> None:
    from zoompy.client import ZoomClient  # type: ignore

    client = ZoomClient(base_url=BASE_URL, token="test")
    aic = getattr(client, "ai_companion")

    missing: list[str] = []
    for case in AIC_CASES:
        method_name = _snake_case(case.operation_id)
        if not hasattr(aic, method_name):
            missing.append(f"{case.operation_id} -> ai_companion.{method_name}()")

    assert not missing, "Missing AI Companion endpoint methods:\n" + "\n".join(missing)


@respx.mock
def test_ai_companion_get_conversation_archives_contract(aic_spec: Mapping[str, Any]) -> None:
    from zoompy.client import ZoomClient  # type: ignore

    schema = _get_json_response_schema(
        aic_spec, "/aic/users/{userId}/conversation_archive", "GET", 200
    )

    payload = _minimal_conversation_archive_response()
    Draft202012Validator(schema).validate(payload)

    user_id = "ABCDEF123456"
    path = _render_path("/aic/users/{userId}/conversation_archive", userId=user_id)

    route = respx.get(f"{BASE_URL}{path}").mock(return_value=httpx.Response(200, json=payload))

    client = ZoomClient(base_url=BASE_URL, token="test")
    aic = getattr(client, "ai_companion")

    fn = getattr(aic, _snake_case("GetAICconversationarchives"))
    result = fn(user_id=user_id)

    assert route.called, "Expected the client to call the AI Companion conversation archive endpoint"
    assert isinstance(result, (dict, list)), "Client should return decoded JSON"

    Draft202012Validator(schema).validate(result)


@respx.mock
def test_ai_companion_raises_on_schema_mismatch(aic_spec: Mapping[str, Any]) -> None:
    from zoompy.client import ZoomClient  # type: ignore

    user_id = "ABCDEF123456"
    path = _render_path("/aic/users/{userId}/conversation_archive", userId=user_id)

    # Intentionally wrong.
    bad_payload = {"nope": True}

    respx.get(f"{BASE_URL}{path}").mock(return_value=httpx.Response(200, json=bad_payload))

    client = ZoomClient(base_url=BASE_URL, token="test")
    aic = getattr(client, "ai_companion")

    fn = getattr(aic, _snake_case("GetAICconversationarchives"))

    with pytest.raises(ValueError):
        fn(user_id=user_id)