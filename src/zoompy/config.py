"""Configuration helpers for the `zoompy` package.

This module exists to keep environment loading and configuration assembly out of
the HTTP client itself. That separation makes the client easier to test and
gives readers one obvious place to look when they need to understand:

- which environment variables are supported
- how `.env` loading works
- which defaults are applied when nothing is configured

The code intentionally avoids external dotenv helpers because the repository's
dependency constraints require `.env` support to be implemented using only the
standard library.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _strip_optional_quotes(value: str) -> str:
    """Remove one matching pair of surrounding quotes from a value.

    A lot of `.env` files contain values written as:

        KEY="value"

    We accept both quoted and unquoted forms so local development stays
    forgiving and unsurprising.
    """

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def discover_project_root(start: Path | None = None) -> Path:
    """Find the repository root by walking upward until `pyproject.toml` exists.

    New contributors often run tests from different working directories. This
    helper makes `.env` lookup more robust than assuming the current directory
    is always the repository root.
    """

    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return current


def load_dotenv(path: Path | None = None) -> None:
    """Load environment variables from a `.env` file without overriding `os.environ`.

    The implementation is intentionally conservative:

    - blank lines are ignored
    - comment lines beginning with `#` are ignored
    - malformed lines are ignored rather than crashing import-time behavior
    - already-defined environment variables always win
    """

    dotenv_path = path or discover_project_root() / ".env"
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, raw_value = line.split("=", 1)
        env_key = key.strip()
        if not env_key or env_key in os.environ:
            continue

        os.environ[env_key] = _strip_optional_quotes(raw_value.strip())


class ZoomSettings(BaseModel):
    """Normalized configuration values used by :class:`zoompy.client.ZoomClient`.

    Pydantic is used here for two reasons:

    1. It gives us a well-typed, explicit container for settings.
    2. It makes future validation or normalization easier to extend without
       smearing configuration logic across the rest of the codebase.
    """

    model_config = ConfigDict(frozen=True)

    account_id: str | None = Field(default=None)
    client_id: str | None = Field(default=None)
    client_secret: str | None = Field(default=None)
    base_url: str = Field(default="https://api.zoom.us/v2")
    oauth_url: str = Field(default="https://zoom.us")
    token_skew_seconds: int = Field(default=60)

    @classmethod
    def from_environment(cls, *, load_local_env: bool = True) -> ZoomSettings:
        """Build settings from process environment variables.

        Parameters
        ----------
        load_local_env:
            When true, attempt to populate missing environment values from a
            repository-root `.env` file first. Existing environment variables
            are never overwritten.
        """

        if load_local_env:
            load_dotenv()

        return cls(
            account_id=os.getenv("ZOOM_ACCOUNT_ID"),
            client_id=os.getenv("ZOOM_CLIENT_ID"),
            client_secret=os.getenv("ZOOM_CLIENT_SECRET"),
            base_url=os.getenv("ZOOM_BASE_URL", "https://api.zoom.us/v2"),
            oauth_url=os.getenv("ZOOM_OAUTH_URL", "https://zoom.us"),
            token_skew_seconds=int(os.getenv("ZOOM_TOKEN_SKEW_SECONDS", "60")),
        )

    def merged_with(self, **overrides: Any) -> ZoomSettings:
        """Return a new settings object with explicit overrides applied.

        This lets the client constructor accept keyword arguments while keeping
        the final configuration assembly logic centralized here.
        """

        data = self.model_dump()
        for key, value in overrides.items():
            if value is not None:
                data[key] = value
        return ZoomSettings(**data)
