"""Focused tests for configuration and `.env` loading helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from zoom_sdk.config import (
    ZoomSettings,
    _strip_optional_quotes,
    discover_project_root,
    load_dotenv,
)


def test_discover_project_root_falls_back_to_start_path(tmp_path: Path) -> None:
    """Return the starting directory when no `pyproject.toml` exists above it."""

    start = tmp_path / "nested" / "project"
    start.mkdir(parents=True)

    assert discover_project_root(start) == start.resolve()


def test_load_dotenv_ignores_missing_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Do nothing when the requested `.env` file is absent."""

    monkeypatch.delenv("ZOOM_ACCOUNT_ID", raising=False)

    load_dotenv(tmp_path / ".env")

    assert "ZOOM_ACCOUNT_ID" not in __import__("os").environ


def test_zoom_settings_can_skip_local_env_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass `.env` discovery when the caller explicitly disables it."""

    monkeypatch.delenv("ZOOM_ACCOUNT_ID", raising=False)

    settings = ZoomSettings.from_environment(load_local_env=False)

    assert settings.account_id is None


def test_zoom_settings_reads_base_url_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Load the primary API base URL override from process environment."""

    monkeypatch.setenv("ZOOM_BASE_URL", "https://api.zoom.example/v2")

    settings = ZoomSettings.from_environment(load_local_env=False)

    assert settings.base_url == "https://api.zoom.example/v2"


def test_strip_optional_quotes_leaves_unquoted_values_unchanged() -> None:
    """Return bare values exactly as written when there is nothing to strip."""

    assert _strip_optional_quotes("plain-value") == "plain-value"


def test_zoom_settings_rejects_insecure_or_malformed_urls() -> None:
    """Reject URL forms that are unsafe for outbound credentialed requests."""

    with pytest.raises(ValueError, match="base_url must use https"):
        ZoomSettings(base_url="http://api.zoom.us/v2")

    with pytest.raises(ValueError, match="oauth_url must not include embedded credentials"):
        ZoomSettings(oauth_url="https://user:pass@zoom.us")

    with pytest.raises(ValueError, match="base_url must not include a query string"):
        ZoomSettings(base_url="https://api.zoom.us/v2?debug=true")

    with pytest.raises(ValueError, match="oauth_url must not include a fragment"):
        ZoomSettings(oauth_url="https://zoom.us#frag")


def test_zoom_settings_rejects_negative_token_skew() -> None:
    """Disallow skew values that would make token expiry accounting ambiguous."""

    with pytest.raises(ValueError, match="token_skew_seconds must be greater than or equal to 0"):
        ZoomSettings(token_skew_seconds=-1)


def test_zoom_settings_rejects_non_integer_env_token_skew(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail early when numeric environment settings are malformed."""

    monkeypatch.setenv("ZOOM_TOKEN_SKEW_SECONDS", "soon")

    with pytest.raises(ValueError, match="ZOOM_TOKEN_SKEW_SECONDS must be an integer"):
        ZoomSettings.from_environment(load_local_env=False)
