from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import httpx
import pytest
import respx
from jsonschema import Draft202012Validator
from jsonschema.validators import validator_for


# These tests define the *contract* for an Accounts API client.
# Implementations should provide a `ZoomClient` class at `zoompy.client`.
#
# The client is expected to:
# - Use https://api.zoom.us/v2 as default base URL (override-able).
# - Send OAuth access tokens via `Authorization: Bearer <token>`.
# - Expose a `.accounts` service with methods matching the endpoints in Accounts.json.
# - Raise `ZoomAPIError` for non-2xx responses.


SCHEMA_RELATIVE_PATHS = [
    # new repo layout: schemas are grouped by category/type
    Path(__file__).resolve().parents[0] / "schemas" / "accounts" / "accounts.json",
    # convenient fallback for this chat environment if you run tests without copying the schema yet
    Path("/mnt/data/Accounts.json"),
]


def _load_openapi_spec() -> dict[str, Any]:
    for p in SCHEMA_RELATIVE_PATHS:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    pytest.skip(
        "Accounts OpenAPI schema not found. Copy the provided accounts.json to src/tests/schemas/accounts/accounts.json"
    )


def _schema_for(spec: dict[str, Any], path: str, method: str, status: str) -> dict[str, Any] | None:
    op = spec["paths"][path][method]
    resp = op["responses"].get(status)
    if not resp:
        return None
    content = (resp.get("content") or {}).get("application/json")
    if not content:
        return None
    return content.get("schema")


def _make_validator(spec: dict[str, Any], schema: Mapping[str, Any]) -> Draft202012Validator:
    # OpenAPI 3.0 schema is *mostly* JSON Schema. jsonschema can validate it well enough
    # for contract tests if we supply the whole document for $ref resolution.
    Validator = validator_for(schema)
    Validator.check_schema(schema)
    return Validator(schema, resolver=Validator.RESOLVER.from_schema(spec))


def _validate(schema: Mapping[str, Any] | None, payload: Any, *, spec: dict[str, Any]) -> None:
    if schema is None:
        pytest.skip("No application/json schema found for this response in Accounts.json")
    v = _make_validator(spec, schema)
    errors = sorted(v.iter_errors(payload), key=lambda e: e.path)
    if errors:
        msg = "\n".join(f"{list(e.path)}: {e.message}" for e in errors[:10])
        raise AssertionError(f"Schema validation failed:\n{msg}")


# ----- Expected public API (tests will enforce it) -----


@dataclass
class _ClientFactory:
    token: str = "test_token"
    base_url: str = "https://api.zoom.us/v2"

    def create(self):
        from zoompy.client import ZoomClient

        return ZoomClient(access_token=self.token, base_url=self.base_url)


@pytest.fixture()
def client_factory() -> _ClientFactory:
    return _ClientFactory()


@pytest.fixture()
def spec() -> dict[str, Any]:
    return _load_openapi_spec()


@pytest.fixture()
def account_id() -> str:
    return "acct_123"


# ----- Core behavior tests -----


def test_client_sets_bearer_auth_header(client_factory: _ClientFactory):
    client = client_factory.create()

    # contract: client must expose an underlying httpx client for testability
    assert hasattr(client, "_http"), "ZoomClient must keep an internal httpx.Client at `._http`"
    assert isinstance(client._http, httpx.Client)

    auth = client._http.headers.get("Authorization")
    assert auth == f"Bearer {client_factory.token}", "Authorization header must be Bearer token"


def test_client_uses_default_base_url(client_factory: _ClientFactory):
    client = client_factory.create()
    assert str(client._http.base_url) == client_factory.base_url


def test_non_2xx_responses_raise_zoom_api_error(client_factory: _ClientFactory, account_id: str):
    client = client_factory.create()

    with respx.mock(assert_all_called=True) as router:
        router.get(f"{client_factory.base_url}/accounts/{account_id}/managed_domains").respond(
            404, json={"message": "Not found"}
        )

        from zoompy.errors import ZoomAPIError

        with pytest.raises(ZoomAPIError) as ei:
            client.accounts.get_managed_domains(account_id)

        err = ei.value
        assert err.status_code == 404
        assert "Not found" in str(err)


# ----- /accounts/{accountId}/managed_domains -----


def test_get_managed_domains_makes_expected_request_and_validates_response(
    client_factory: _ClientFactory, account_id: str, spec: dict[str, Any]
):
    client = client_factory.create()

    payload = {
        "domains": [{"domain": "example.com", "status": "verified"}],
        "total_records": 1,
    }

    with respx.mock(assert_all_called=True) as router:
        route = router.get(
            f"{client_factory.base_url}/accounts/{account_id}/managed_domains"
        ).respond(200, json=payload)

        result = client.accounts.get_managed_domains(account_id)

        assert route.called
        assert result == payload

    schema = _schema_for(spec, "/accounts/{accountId}/managed_domains", "get", "200")
    _validate(schema, result, spec=spec)


# ----- /accounts/{accountId}/trusted_domains -----


def test_get_trusted_domains_validates_response(
    client_factory: _ClientFactory, account_id: str, spec: dict[str, Any]
):
    client = client_factory.create()

    payload = {"trusted_domains": ["example.com", "corp.internal"]}

    with respx.mock(assert_all_called=True) as router:
        router.get(
            f"{client_factory.base_url}/accounts/{account_id}/trusted_domains"
        ).respond(200, json=payload)

        result = client.accounts.get_trusted_domains(account_id)
        assert result == payload

    schema = _schema_for(spec, "/accounts/{accountId}/trusted_domains", "get", "200")
    _validate(schema, result, spec=spec)


# ----- /accounts/{accountId}/lock_settings (GET + PATCH) -----


def test_get_lock_settings_supports_query_params(
    client_factory: _ClientFactory, account_id: str
):
    client = client_factory.create()

    payload = {"audio_conferencing": {"toll_call": True}}

    with respx.mock(assert_all_called=True) as router:
        route = router.get(
            f"{client_factory.base_url}/accounts/{account_id}/lock_settings",
            params={"option": "meeting_security", "custom_query_fields": "audio_conferencing"},
        ).respond(200, json=payload)

        result = client.accounts.get_lock_settings(
            account_id,
            option="meeting_security",
            custom_query_fields=["audio_conferencing"],
        )

        assert route.called
        assert result == payload


def test_patch_lock_settings_sends_json_and_returns_response(
    client_factory: _ClientFactory, account_id: str
):
    client = client_factory.create()

    body = {"audio_conferencing": {"toll_call": False}}
    payload = {"audio_conferencing": {"toll_call": False}}

    with respx.mock(assert_all_called=True) as router:
        route = router.patch(
            f"{client_factory.base_url}/accounts/{account_id}/lock_settings"
        ).respond(200, json=payload)

        result = client.accounts.update_lock_settings(account_id, body)

        assert route.called
        sent = json.loads(route.calls[0].request.content.decode("utf-8"))
        assert sent == body
        assert result == payload


# ----- /accounts/{accountId}/settings (GET + PATCH) -----


def test_get_account_settings_supports_query_params(
    client_factory: _ClientFactory, account_id: str
):
    client = client_factory.create()

    payload = {"schedule_meeting": {"host_video": False}}

    with respx.mock(assert_all_called=True) as router:
        route = router.get(
            f"{client_factory.base_url}/accounts/{account_id}/settings",
            params={"option": "meeting_security", "custom_query_fields": "schedule_meeting"},
        ).respond(200, json=payload)

        result = client.accounts.get_settings(
            account_id,
            option="meeting_security",
            custom_query_fields=["schedule_meeting"],
        )

        assert route.called
        assert result == payload


def test_patch_account_settings_returns_none_on_204(
    client_factory: _ClientFactory, account_id: str
):
    client = client_factory.create()

    body = {"schedule_meeting": {"host_video": True}}

    with respx.mock(assert_all_called=True) as router:
        route = router.patch(
            f"{client_factory.base_url}/accounts/{account_id}/settings",
            params={"option": "meeting_security"},
        ).respond(204)

        result = client.accounts.update_settings(account_id, body, option="meeting_security")

        assert route.called
        sent = json.loads(route.calls[0].request.content.decode("utf-8"))
        assert sent == body
        assert result is None


# ----- /accounts/{accountId}/settings/registration (GET + PATCH) -----


def test_get_registration_settings_type_query_param_required_by_contract(
    client_factory: _ClientFactory, account_id: str, spec: dict[str, Any]
):
    client = client_factory.create()

    payload = {
        "options": {"host_email_notification": True, "close_registration": True},
        "questions": [],
    }

    with respx.mock(assert_all_called=True) as router:
        route = router.get(
            f"{client_factory.base_url}/accounts/{account_id}/settings/registration",
            params={"type": "webinar"},
        ).respond(200, json=payload)

        result = client.accounts.get_registration_settings(account_id, type="webinar")

        assert route.called
        assert result == payload

    schema = _schema_for(spec, "/accounts/{accountId}/settings/registration", "get", "200")
    _validate(schema, result, spec=spec)


def test_patch_registration_settings_returns_none_on_204(
    client_factory: _ClientFactory, account_id: str
):
    client = client_factory.create()

    body = {"options": {"close_registration": False}}

    with respx.mock(assert_all_called=True) as router:
        route = router.patch(
            f"{client_factory.base_url}/accounts/{account_id}/settings/registration",
            params={"type": "webinar"},
        ).respond(204)

        result = client.accounts.update_registration_settings(
            account_id, type="webinar", data=body
        )

        assert route.called
        sent = json.loads(route.calls[0].request.content.decode("utf-8"))
        assert sent == body
        assert result is None


# ----- /accounts/{accountId}/owner (PUT) -----


def test_update_account_owner_puts_email_and_returns_none_on_204(
    client_factory: _ClientFactory, account_id: str
):
    client = client_factory.create()

    with respx.mock(assert_all_called=True) as router:
        route = router.put(
            f"{client_factory.base_url}/accounts/{account_id}/owner"
        ).respond(204)

        result = client.accounts.update_owner(account_id, email="admin@example.com")

        assert route.called
        sent = json.loads(route.calls[0].request.content.decode("utf-8"))
        assert sent == {"email": "admin@example.com"}
        assert result is None


# ----- /accounts/{accountId}/settings/virtual_backgrounds (POST + DELETE) -----


def test_upload_virtual_background_posts_multipart_and_validates_response(
    client_factory: _ClientFactory, account_id: str, spec: dict[str, Any]
):
    client = client_factory.create()

    file_bytes = b"fake image bytes"
    fileobj = io.BytesIO(file_bytes)

    payload = {
        "id": "_l0MP1U7Qn2JgJ4oEJbVZQ",
        "is_default": False,
        "name": "profile.PNG",
        "size": 7221,
        "type": "image",
    }

    with respx.mock(assert_all_called=True) as router:
        route = router.post(
            f"{client_factory.base_url}/accounts/{account_id}/settings/virtual_backgrounds"
        ).respond(201, json=payload)

        result = client.accounts.upload_virtual_background(
            account_id,
            filename="profile.PNG",
            file=fileobj,
            content_type="image/png",
        )

        assert route.called
        req = route.calls[0].request
        ctype = req.headers.get("Content-Type", "")
        assert ctype.startswith(
            "multipart/form-data;"
        ), "upload_virtual_background must send multipart/form-data"
        assert b"profile.PNG" in req.content
        assert result == payload

    schema = _schema_for(spec, "/accounts/{accountId}/settings/virtual_backgrounds", "post", "201")
    _validate(schema, result, spec=spec)


def test_delete_virtual_backgrounds_accepts_iterable_and_uses_comma_separated_query(
    client_factory: _ClientFactory, account_id: str
):
    client = client_factory.create()

    file_ids = ["id1", "id2", "id3"]

    with respx.mock(assert_all_called=True) as router:
        route = router.delete(
            f"{client_factory.base_url}/accounts/{account_id}/settings/virtual_backgrounds",
            params={"file_ids": ",".join(file_ids)},
        ).respond(204)

        result = client.accounts.delete_virtual_backgrounds(account_id, file_ids=file_ids)

        assert route.called
        assert result is None


# ----- Type and ergonomics contracts -----


def test_custom_query_fields_can_be_string_or_iterable(client_factory: _ClientFactory, account_id: str):
    client = client_factory.create()

    with respx.mock(assert_all_called=True) as router:
        router.get(
            f"{client_factory.base_url}/accounts/{account_id}/settings",
            params={"custom_query_fields": "in_meeting"},
        ).respond(200, json={})

        assert client.accounts.get_settings(account_id, custom_query_fields="in_meeting") == {}

    with respx.mock(assert_all_called=True) as router:
        router.get(
            f"{client_factory.base_url}/accounts/{account_id}/settings",
            params={"custom_query_fields": "in_meeting,recording"},
        ).respond(200, json={})

        assert (
            client.accounts.get_settings(account_id, custom_query_fields=["in_meeting", "recording"]) == {}
        )
