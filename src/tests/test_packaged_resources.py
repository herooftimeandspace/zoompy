"""Package-resource availability tests for bundled schema assets.

The client depends on JSON documents shipped inside the installed package.
These tests verify that the package can discover those assets through
`importlib.resources`, not only through direct repository paths.
"""

from __future__ import annotations

from importlib import resources
from typing import Any


def _iter_json_files(root: Any) -> list[Any]:
    """Collect JSON children from an `importlib.resources` traversable tree."""

    collected: list[Any] = []
    for child in root.iterdir():
        if child.is_dir():
            collected.extend(_iter_json_files(child))
        elif child.name.endswith(".json"):
            collected.append(child)
    return collected


def test_packaged_endpoint_resources_are_available() -> None:
    """Ensure endpoint schema files are discoverable as package resources."""

    root = resources.files("zoom_sdk") / "endpoints"
    files = _iter_json_files(root)
    assert files


def test_packaged_webhook_resources_are_available() -> None:
    """Ensure webhook schema files are discoverable as package resources."""

    root = resources.files("zoom_sdk") / "webhooks"
    files = _iter_json_files(root)
    assert files


def test_packaged_master_account_resources_are_discoverable() -> None:
    """Ensure the master-account resource tree exists in the package.

    The tree may be empty until the repository syncs real `ma/master.json`
    documents, but the packaged directory itself still needs to be present so
    the runtime schema loader has a stable location to inspect.
    """

    root = resources.files("zoom_sdk") / "master_accounts"
    children = list(root.iterdir())
    assert children is not None


def test_packaged_sideloaded_resources_are_not_shipped() -> None:
    """Ensure unsupported sideloaded schema JSON files are not packaged."""

    root = resources.files("zoom_sdk") / "sideloaded"
    files = _iter_json_files(root) if root.is_dir() else []
    assert not files
