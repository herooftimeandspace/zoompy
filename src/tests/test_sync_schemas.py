"""Focused tests for the manifest-driven schema synchronization workflow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.sync_schemas import (
    SchemaSource,
    download_openapi_specs,
    expand_schema_sources,
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


def test_manifest_rejects_target_title_without_expected_title(
    tmp_path: Path,
) -> None:
    """Require the publisher identity to be checked before retitling."""

    manifest_path = tmp_path / "schema_urls.json"
    _write_manifest(
        manifest_path,
        {
            "urls": [
                {
                    "url": "https://example.test/chat/methods/endpoints.json",
                    "target_title": "Team Chat",
                }
            ]
        },
    )

    with pytest.raises(SystemExit, match="requires 'expected_title'"):
        load_manifest(manifest_path)


def test_download_rejects_unexpected_title_before_normalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not disguise an unrelated publisher document as a known schema."""

    payload: dict[str, Any] = {
        "openapi": "3.1.1",
        "info": {"title": "Meetings"},
        "paths": {},
    }
    monkeypatch.setattr(
        "scripts.sync_schemas.fetch_json",
        lambda _url, _timeout: payload,
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

    assert downloaded == []
    assert failures[0].reason == "expected title 'Chat', got 'Meetings'"
    assert payload["info"]["title"] == "Meetings"


def test_explicit_companion_title_is_not_rewritten_to_endpoint_title() -> None:
    """Preserve companion metadata when it differs from the endpoint title."""

    sources = expand_schema_sources(
        SchemaSource(
            url="https://example.test/iq/methods/endpoints.json",
            expected_title="Revenue Accelerator",
            target_title="Revenue Accelerator",
        ),
        webhook_expected_title="Zoom Revenue Accelerator Webhooks",
        master_account_expected_title="Revenue Accelerator Master Account",
    )

    assert sources[1].expected_title == "Zoom Revenue Accelerator Webhooks"
    assert sources[1].target_title == "Zoom Revenue Accelerator Webhooks"
    assert sources[1].mapping_title == "Revenue Accelerator"
    assert sources[2].expected_title == "Revenue Accelerator Master Account"
    assert sources[2].target_title == "Revenue Accelerator Master Account"
    assert sources[2].mapping_title == "Revenue Accelerator"


def test_download_preserves_explicit_companion_title_while_mapping_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep companion metadata while using its endpoint title to select a path."""

    payload = {
        "openapi": "3.1.1",
        "info": {"title": "Zoom Revenue Accelerator Webhooks"},
        "webhooks": {},
    }
    monkeypatch.setattr(
        "scripts.sync_schemas.fetch_json",
        lambda _url, _timeout: payload,
    )
    source = expand_schema_sources(
        SchemaSource(
            url="https://example.test/iq/methods/endpoints.json",
            expected_title="Revenue Accelerator",
            target_title="Revenue Accelerator",
        ),
        webhook_expected_title="Zoom Revenue Accelerator Webhooks",
    )[1]

    downloaded, failures = download_openapi_specs([source], timeout=1)

    assert failures == []
    assert downloaded[0].title == "Revenue Accelerator"
    assert (
        downloaded[0].payload["info"]["title"]
        == "Zoom Revenue Accelerator Webhooks"
    )


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
