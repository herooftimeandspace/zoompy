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


def test_strip_optional_quotes_leaves_unquoted_values_unchanged() -> None:
    """Return bare values exactly as written when there is nothing to strip."""

    assert _strip_optional_quotes("plain-value") == "plain-value"
