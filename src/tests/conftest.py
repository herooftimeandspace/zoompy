"""Shared pytest fixtures for the schema-driven contract suites.

The contract tests expect endpoint-specific fixtures such as `accounts_client`
or `team_chat_client`. All of those fixtures ultimately delegate to the same
production `ZoomClient.request` method, so this file centralizes the wiring.

Keeping the fixture layer thin is deliberate: the goal is to validate the real
library behavior, not a parallel testing adapter with its own custom logic.
"""

# ruff: noqa: E402, I001

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

# Pytest loads `conftest.py` before importing the package under test. When the
# developer runs pytest directly from the repository root without first doing an
# editable install, `src/` is not automatically on `sys.path`, so `import
# zoompy` fails. We add the repository `src` directory here to make local test
# runs behave the same way as an editable install.
PROJECT_SRC = Path(__file__).resolve().parents[1]
if str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

from zoompy import ZoomClient


RequestCallable = Callable[..., dict[str, Any] | list[Any] | None]


@pytest.fixture(scope="session")
def zoom_client() -> Iterator[ZoomClient]:
    """Create one reusable client for the contract tests.

    We pass a fixed `access_token` override so tests never attempt to perform a
    live OAuth flow. The contract suites mock outbound HTTP with `respx`, so the
    token only needs to exist as a placeholder authorization value.
    """

    client = ZoomClient(access_token="test-access-token")
    try:
        yield client
    finally:
        client.close()


def _request_callable(client: ZoomClient) -> RequestCallable:
    """Return the exact callable shape the contract tests expect."""

    return client.request


_CLIENT_FIXTURE_NAMES = (
    "accounts_client",
    "ai_companion_client",
    "auto_dialer_client",
    "calendar_client",
    "chatbot_client",
    "clips_client",
    "cobrowse_sdk_client",
    "commerce_client",
    "conference_room_connector_client",
    "contact_center_client",
    "events_client",
    "healthcare_client",
    "mail_client",
    "marketplace_client",
    "meetings_client",
    "number_management_client",
    "phone_client",
    "qss_client",
    "quality_management_client",
    "revenue_accelerator_client",
    "rooms_client",
    "scheduler_client",
    "scim_client",
    "tasks_client",
    "team_chat_client",
    "users_client",
    "video_management_client",
    "video_sdk_client",
    "virtual_agent_client",
    "whiteboard_client",
    "workforce_client",
    "zoom_docs_client",
)


def _make_client_fixture(name: str) -> Any:
    """Create one tiny pytest fixture that aliases `zoom_client.request`.

    The contract suites intentionally preserve many historical fixture names so
    the schema-family tests stay readable. Generating the fixtures from one list
    keeps that compatibility without maintaining dozens of identical functions.
    """

    @pytest.fixture(name=name)
    def _fixture(zoom_client: ZoomClient) -> RequestCallable:
        return _request_callable(zoom_client)

    _fixture.__name__ = name
    return _fixture


for _fixture_name in _CLIENT_FIXTURE_NAMES:
    globals()[_fixture_name] = _make_client_fixture(_fixture_name)

del _fixture_name
