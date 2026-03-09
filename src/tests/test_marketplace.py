

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import httpx
import pytest
import respx
from jsonschema import Draft202012Validator


# These are *contract tests*.
# To run them against your implementation, provide a fixture named `marketplace_api` that returns
# an object exposing a `request(method: str, path: str, **kwargs) -> Any` method.
#
# Recommended shape:
# - `path` is a Zoom API path starting with `/v2/...` or `/...`.
# - Return value may be:
#   - an httpx.Response
#   - OR parsed JSON (dict/list)
#
# If you return an httpx.Response, tests will call `.json()`.


SCHEMA_PATH = Path("src/tests/schemas/marketplace/Marketplace.json")
BASE_URL = "https://api.zoom.us/v2"


@dataclass(frozen=True)
class Case:
    name: str
    method: str
    path: str
    status_code: int
    # kwargs to pass into marketplace_api.request(...)
    request_kwargs: dict[str, Any]
    # mocked response JSON (or None for empty body)
    response_json: Any | None


@pytest.fixture(scope="session")
def marketplace_openapi() -> dict[str, Any]:
    if not SCHEMA_PATH.exists():
        raise AssertionError(f"Missing schema file at {SCHEMA_PATH!s}")
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _get_operation(spec: Mapping[str, Any], path: str, method: str) -> Mapping[str, Any]:
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        raise AssertionError("OpenAPI schema missing 'paths' object")

    item = paths.get(path)
    if not isinstance(item, dict):
        raise AssertionError(f"OpenAPI schema missing path: {path}")

    op = item.get(method.lower())
    if not isinstance(op, dict):
        raise AssertionError(f"OpenAPI schema missing operation: {method.upper()} {path}")

    return op


def _get_json_schema_for_response(
    spec: Mapping[str, Any], path: str, method: str, status_code: int
) -> Mapping[str, Any] | None:
    op = _get_operation(spec, path, method)
    responses = op.get("responses")
    if not isinstance(responses, dict):
        raise AssertionError(f"Operation has no responses map: {method.upper()} {path}")

    resp = responses.get(str(status_code))
    if not isinstance(resp, dict):
        # Some specs only document a subset. For contract tests we require declared success.
        raise AssertionError(
            f"Schema missing documented response {status_code} for {method.upper()} {path}"
        )

    content = resp.get("content")
    if not content:
        return None

    app_json = content.get("application/json")
    if not isinstance(app_json, dict):
        return None

    schema = app_json.get("schema")
    if not isinstance(schema, dict):
        return None

    return schema


def _validate_json(instance: Any, schema: Mapping[str, Any], context: str) -> None:
    # OpenAPI schemas are *mostly* JSON Schema-ish; this validator catches common regressions.
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: e.path)
    if errors:
        formatted = "\n".join(
            f"- {list(err.path)}: {err.message}" for err in errors[:20]
        )
        raise AssertionError(f"Schema validation failed for {context}:\n{formatted}")


def _coerce_response_json(result: Any) -> Any | None:
    if result is None:
        return None
    if isinstance(result, httpx.Response):
        if result.status_code == 204:
            return None
        # Some implementations may return empty on 204/202.
        try:
            return result.json()
        except Exception:
            return None
    return result


def _assert_last_request(
    router: respx.Router, *, method: str, url: str, expected_json: Any | None = None
) -> None:
    calls = router.calls
    assert calls, "Expected an HTTP request to be made, but none were recorded"
    req = calls[-1].request
    assert req.method == method.upper()
    assert str(req.url) == url

    if expected_json is not None:
        body = req.content
        assert body, "Expected request body JSON, but request body was empty"
        parsed = json.loads(body.decode("utf-8"))
        assert parsed == expected_json


@pytest.fixture
def marketplace_api() -> Any:
    # Intentionally forces the implementer to provide a fixture in their conftest.
    raise NotImplementedError(
        "Provide a `marketplace_api` fixture that returns an object with a .request(method, path, **kwargs) method."
    )


def _cases() -> list[Case]:
    return [
        Case(
            name="list_apps",
            method="GET",
            path="/marketplace/apps",
            status_code=200,
            request_kwargs={
                "params": {"page_size": 30, "type": "active_requests"},
            },
            response_json={
                "page_size": 30,
                "next_page_token": "w7587w4eiyfsudgf",
                "apps": [
                    {
                        "app_id": "nqSreNVsQ2eGzUMGnA8AHA",
                        "app_name": "Example App",
                        "app_type": "ZoomApp",
                        "app_usage": 1,
                        "app_status": "PUBLISHED",
                        "request_id": "zZLSQoL6S0OB6asaZ3zAOQ",
                        "request_total_number": 8,
                        "request_pending_number": 3,
                        "app_developer_type": "THIRD_PARTY",
                        "app_description": "information about the app.",
                        "app_icon": "https://marketplacecontent.zoom.us/example.png",
                        "app_privacy_policy_url": "https://www.example.com/privacy/policy",
                        "app_directory_url": "https://marketplace.zoom.us/apps/{appId}",
                        "app_help_url": "https://www.example.com/support",
                        "approval_info": {
                            "approved_type": "forSpecificUser",
                            "approver_id": "1gAKvx8fSUeTg-mHgmYTRA",
                            "approved_time": "2024-03-21T14:32:28Z",
                            "app_approval_closed": True,
                        },
                    }
                ],
            },
        ),
        Case(
            name="create_apps",
            method="POST",
            path="/marketplace/apps",
            status_code=201,
            request_kwargs={
                "json": {
                    "app_type": "s2s_oauth",
                    "app_name": "My App",
                    "company_name": "ZOOM",
                    "contact_email": "example@example.com",
                    "contact_name": "ZOOM",
                    "active": False,
                    "scopes": ["meeting:read"],
                }
            },
            response_json={
                "created_at": "2023-02-16T17:32:28Z",
                "app_id": "Bwpq5zXvQr-4PKhtYOD23g",
                "app_name": "My App",
                "app_type": "s2s_oauth",
                "scopes": ["meeting:read"],
                "development_credentials": {
                    "client_id": "2J5nVOGXQXCvXoRWyGQDow",
                    "client_secret": "BzFOrwgq1Gw6KCsyeYnmETU5e4zakGwQ",
                },
            },
        ),
        Case(
            name="get_app_info",
            method="GET",
            path="/marketplace/apps/{appId}",
            status_code=200,
            request_kwargs={},
            response_json={
                "app_id": "nqSreNVsQ2eGzUMGnA8AHA",
                "app_name": "Example App",
                "app_description": "Example App description",
                "app_type": "ZoomApp",
                "app_usage": 1,
                "app_status": "PUBLISHED",
                "app_links": {
                    "documentation_url": "https://xxxx",
                    "privacy_policy_url": "https://xxxx",
                    "support_url": "https://xxxx",
                    "terms_of_use_url": "https://xxxx",
                },
                "app_scopes": ["meeting:read"],
            },
        ),
        Case(
            name="delete_app",
            method="DELETE",
            path="/marketplace/apps/{appId}",
            status_code=204,
            request_kwargs={},
            response_json=None,
        ),
        Case(
            name="get_api_call_logs",
            method="GET",
            path="/marketplace/apps/{appId}/api_call_logs",
            status_code=200,
            request_kwargs={
                "params": {"page_size": 30, "duration": 7, "method": "GET"},
            },
            response_json={
                "page_size": 30,
                "next_page_token": "b43YBRLJFg3V4vsSpxvGdKIGtNbxn9h9If2",
                "call_logs": [
                    {
                        "url_pattern": "https://zoom.us/v2/users/_DkLL5iMRW-p_6yhPocD4Q/assistants",
                        "time": "1737704112808",
                        "http_status": 200,
                        "method": "GET",
                        "trace_id": "v=2.0;clid=aw1;rid=ZTP_10094594754_scenario_10000000934_aw1_20250124T073509485Z",
                    }
                ],
            },
        ),
        Case(
            name="generate_zoom_app_deeplink",
            method="POST",
            path="/marketplace/apps/{appId}/deeplink",
            status_code=201,
            request_kwargs={
                "json": {"type": 1, "target": "meeting", "action": "openPageX"}
            },
            response_json={
                "deeplink": "https://zoom.us/launch/chatapp/example",
            },
        ),
        Case(
            name="send_app_notifications",
            method="POST",
            path="/app/notifications",
            status_code=202,
            request_kwargs={
                "json": {
                    "notification_id": "0HTc4gWHRSCef82_KlfCOQ",
                    "message": {"text": "hello, world!"},
                    "user_id": "uGjCIsQsQ_KKW20xAkiHyw",
                }
            },
            response_json=None,
        ),
        Case(
            name="validate_manifest",
            method="POST",
            path="/marketplace/apps/manifest/validate",
            status_code=200,
            request_kwargs={"json": {"manifest": {"name": "myapp"}}},
            response_json={
                "ok": False,
                "error": "invalid_manifest",
                "errors": [
                    {
                        "message": "Event Subscription requires either Request URL or Socket Mode Enabled",
                        "setting": "/settings/event_subscriptions",
                    }
                ],
            },
        ),
        Case(
            name="get_app_manifest",
            method="GET",
            path="/marketplace/apps/{appId}/manifest",
            status_code=200,
            request_kwargs={},
            response_json={"manifest": {"name": "myapp"}},
        ),
        Case(
            name="update_app_by_manifest",
            method="PUT",
            path="/marketplace/apps/{appId}/manifest",
            status_code=204,
            request_kwargs={"json": {"manifest": {"name": "myapp"}}},
            response_json=None,
        ),
        Case(
            name="get_app_user_entitlements",
            method="GET",
            path="/marketplace/monetization/entitlements",
            status_code=200,
            request_kwargs={"params": {"user_id": "f1sTWCMaTmOIZxLMlmHvEQ"}},
            response_json=[
                {
                    "id": "123e4567-e89b-12d3-a456-426655440000",
                    "plan_name": "PRO",
                    "plan_id": "f3318144-b66c-4c1e-a987-ebc224e2b706",
                }
            ],
        ),
        Case(
            name="get_user_app_requests",
            method="GET",
            path="/marketplace/users/{userId}/apps",
            status_code=200,
            request_kwargs={"params": {"page_size": 30, "type": "active_requests"}},
            response_json={
                "page_size": 30,
                "next_page_token": "w7587w4eiyfsudgf",
                "apps": [
                    {
                        "app_id": "nqSreNVsQ2eGzUMGnA8AHA",
                        "app_name": "Example App",
                        "app_type": "ZoomApp",
                        "app_usage": 1,
                        "app_status": "PUBLISHED",
                        "request_id": "zZLSQoL6S0OB6asaZ3zAOQ",
                        "request_date_time": "2021-07-21T17:32:28Z",
                        "request_status": "pending",
                    }
                ],
            },
        ),
        Case(
            name="enable_disable_user_app_subscription",
            method="PATCH",
            path="/marketplace/users/{userId}/apps/{appId}/subscription",
            status_code=204,
            request_kwargs={"json": {"action": "enable"}},
            response_json=None,
        ),
        Case(
            name="get_user_entitlements",
            method="GET",
            path="/marketplace/users/{userId}/entitlements",
            status_code=200,
            request_kwargs={},
            response_json={"entitlements": [{"entitlement_id": 1}]},
        ),
        Case(
            name="generate_app_deeplink",
            method="POST",
            path="/zoomapp/deeplink",
            status_code=200,
            request_kwargs={"json": {"type": 1, "action": "openPageX", "user_id": "D40dy5L7SJiSTayIvRV9Lw"}},
            response_json={"deeplink": "https://zoom.us/launch/chatapp/example"},
        ),
        Case(
            name="get_custom_field_values",
            method="GET",
            path="/marketplace/app/custom_fields",
            status_code=200,
            request_kwargs={},
            response_json=[
                {
                    "field_id": "city_name",
                    "type": "plain_text_input",
                    "value": "San Jose",
                }
            ],
        ),
    ]


def _render_path(path_template: str) -> str:
    # Use deterministic placeholders.
    return (
        path_template.replace("{appId}", "nqSreNVsQ2eGzUMGnA8AHA")
        .replace("{userId}", "f1sTWCMaTmOIZxLMlmHvEQ")
        .replace("{eventSubscriptionId}", "0ZAaJY4dQ52BbwI9PArBLQ")
    )


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c.name)
def test_marketplace_contract_success(
    marketplace_api: Any, marketplace_openapi: dict[str, Any], case: Case
) -> None:
    rendered_path = _render_path(case.path)
    url = f"{BASE_URL}{rendered_path}"

    # Setup mock transport.
    with respx.mock(assert_all_called=False, assert_all_mocked=False) as router:
        route = router.request(case.method, url)
        if case.status_code == 204 or case.response_json is None:
            route.mock(return_value=httpx.Response(case.status_code))
        else:
            route.mock(return_value=httpx.Response(case.status_code, json=case.response_json))

        result = marketplace_api.request(case.method, rendered_path, **case.request_kwargs)
        parsed = _coerce_response_json(result)

        # Validate response JSON against OpenAPI response schema (when schema exists).
        schema = _get_json_schema_for_response(
            marketplace_openapi, case.path, case.method, case.status_code
        )
        if schema is not None:
            assert parsed is not None, "Expected JSON response body, got none"
            _validate_json(parsed, schema, f"{case.method} {case.path} -> {case.status_code}")

        # Basic request assertion: correct method + URL, plus JSON body when provided.
        expected_body = case.request_kwargs.get("json")
        _assert_last_request(router, method=case.method, url=url, expected_json=expected_body)


def test_marketplace_schema_contains_expected_endpoints(marketplace_openapi: dict[str, Any]) -> None:
    # If this fails, the schema moved/changed. Better to know now than in prod.
    expected: list[tuple[str, str]] = [
        ("POST", "/app/notifications"),
        ("GET", "/marketplace/apps"),
        ("POST", "/marketplace/apps"),
        ("GET", "/marketplace/apps/{appId}"),
        ("DELETE", "/marketplace/apps/{appId}"),
        ("GET", "/marketplace/apps/{appId}/api_call_logs"),
        ("POST", "/marketplace/apps/{appId}/deeplink"),
        ("POST", "/marketplace/apps/manifest/validate"),
        ("GET", "/marketplace/apps/{appId}/manifest"),
        ("PUT", "/marketplace/apps/{appId}/manifest"),
        ("GET", "/marketplace/monetization/entitlements"),
        ("GET", "/marketplace/users/{userId}/apps"),
        ("PATCH", "/marketplace/users/{userId}/apps/{appId}/subscription"),
        ("GET", "/marketplace/users/{userId}/entitlements"),
        ("POST", "/zoomapp/deeplink"),
        ("GET", "/marketplace/app/custom_fields"),
    ]

    for method, path in expected:
        op = _get_operation(marketplace_openapi, path, method)
        assert op.get("operationId"), f"Missing operationId for {method} {path}"


def test_marketplace_rejects_undocumented_success_response(marketplace_openapi: dict[str, Any]) -> None:
    # Guards against implementations silently treating 200 as success where spec says 201/202/204.
    with pytest.raises(AssertionError):
        _get_json_schema_for_response(marketplace_openapi, "/marketplace/apps", "POST", 200)