"""Focused tests for the manifest-driven schema synchronization workflow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.sync_schemas import (
    SchemaSource,
    download_openapi_specs,
    load_manifest,
    validate_retained_schemas,
)


def _write_manifest(path: Path, payload: dict[str, object]) -> None:
    """Write one temporary schema manifest for parser tests."""

    path.write_text(json.dumps(payload), encoding="utf-8")


def test_manifest_separates_published_title_from_compatibility_title(
    tmp_path: Path,
) -> None:
    """Keep a stable local title when Zoom renames a published schema."""

    manifest_path = tmp_path / "schema_urls.json"
    _write_manifest(
        manifest_path,
        {
            "urls": [
                {
                    "url": "https://example.test/chat/methods/endpoints.json",
                    "expected_title": "Chat",
                    "target_title": "Team Chat",
                }
            ]
        },
    )

    manifest = load_manifest(manifest_path)

    assert manifest.sources[0] == SchemaSource(
        url="https://example.test/chat/methods/endpoints.json",
        expected_title="Chat",
        target_title="Team Chat",
    )


def test_download_normalizes_renamed_schema_to_compatibility_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Write a renamed published schema under its stable SDK title."""

    monkeypatch.setattr(
        "scripts.sync_schemas.fetch_json",
        lambda _url, _timeout: {
            "openapi": "3.1.1",
            "info": {"title": "Chat"},
            "paths": {},
        },
    )

    downloaded, failures = download_openapi_specs(
        [
            SchemaSource(
                url="https://example.test/chat/methods/endpoints.json",
                expected_title="Chat",
                target_title="Team Chat",
            )
        ],
        timeout=1,
    )

    assert failures == []
    assert downloaded[0].title == "Team Chat"
    assert downloaded[0].payload["info"]["title"] == "Team Chat"


def test_retained_schema_must_still_exist_locally(tmp_path: Path) -> None:
    """Fail closed if a schema marked for retention disappears."""

    manifest_path = tmp_path / "schema_urls.json"
    _write_manifest(
        manifest_path,
        {
            "urls": ["https://example.test/users/methods/endpoints.json"],
            "retained": [
                {
                    "title": "Zoom Docs",
                    "former_url": "https://example.test/zoom-docs.json",
                    "reason": "The publisher withdrew the schema.",
                }
            ],
        },
    )
    manifest = load_manifest(manifest_path)
    schema_path = tmp_path / "workplace" / "Zoom Docs.json"
    schema_path.parent.mkdir()
    schema_path.write_text(
        json.dumps({"info": {"title": "Zoom Docs"}}),
        encoding="utf-8",
    )

    validate_retained_schemas(tmp_path, manifest.retained)
    schema_path.unlink()

    with pytest.raises(SystemExit, match="Zoom Docs.*missing"):
        validate_retained_schemas(tmp_path, manifest.retained)
