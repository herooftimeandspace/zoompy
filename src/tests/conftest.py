"""Shared pytest fixtures for the schema-driven contract suites.

The contract tests expect endpoint-specific fixtures such as `accounts_client`
or `team_chat_client`. All of those fixtures ultimately delegate to the same
production `ZoomClient.request` method, so this file centralizes the wiring.

Keeping the fixture layer thin is deliberate: the goal is to validate the real
library behavior, not a parallel testing adapter with its own custom logic.
"""

# ruff: noqa: E402, I001

from __future__ import annotations

from collections.abc import Callable, Iterator
import sys
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


# Each endpoint fixture is intentionally a tiny alias to the same client
# request method. The distinct fixture names are preserved because the existing
# tests already depend on them and we do not want to restructure the suites.
@pytest.fixture
def accounts_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def ai_companion_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def auto_dialer_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def calendar_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def chatbot_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def clips_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def cobrowse_sdk_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def commerce_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def conference_room_connector_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def contact_center_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def events_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def healthcare_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def mail_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def marketplace_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def meetings_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def number_management_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def phone_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def qss_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def quality_management_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def revenue_accelerator_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def rooms_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def scheduler_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def scim_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def tasks_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def team_chat_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def users_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def video_management_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def video_sdk_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def virtual_agent_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def whiteboard_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def workforce_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)


@pytest.fixture
def zoom_docs_client(zoom_client: ZoomClient) -> RequestCallable:
    return _request_callable(zoom_client)
