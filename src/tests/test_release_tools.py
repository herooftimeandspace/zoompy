"""Tests for deterministic Python promotion and release helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.release_tools import (
    AssociatedPull,
    bump_version,
    read_project_version,
    resolve_promotion_range_semver_label,
    resolve_promotion_semver_label,
    select_semver_label,
    update_version_files,
)


@pytest.mark.parametrize(
    ("current", "part", "expected"),
    [
        ("1.2.3", "patch", "1.2.4"),
        ("v1.2.3", "minor", "1.3.0"),
        ("1.2.3", "major", "2.0.0"),
    ],
)
def test_bump_version(current: str, part: str, expected: str) -> None:
    """Increment each supported semantic-version component correctly."""

    assert bump_version(current, part) == expected


@pytest.mark.parametrize(
    ("current", "part", "message"),
    [
        ("1.2", "patch", "Unsupported version format"),
        ("1.2.3", "build", "Unsupported semantic-version part"),
    ],
)
def test_bump_version_rejects_invalid_input(
    current: str,
    part: str,
    message: str,
) -> None:
    """Fail closed rather than guessing at unsupported release metadata."""

    with pytest.raises(ValueError, match=message):
        bump_version(current, part)


def test_select_semver_label_defaults_and_rejects_ambiguity() -> None:
    """Resolve zero or one release label and reject conflicting labels."""

    assert select_semver_label(["enhancement"]) == "semver:patch"
    assert select_semver_label(["semver:minor"]) == "semver:minor"
    assert select_semver_label(["semver:major", "semver:major"]) == "semver:major"
    with pytest.raises(ValueError, match="at most one"):
        select_semver_label(["semver:patch", "semver:minor"])


def test_resolve_promotion_semver_prefers_dev_source_pull() -> None:
    """Ignore later promotion associations when a source PR is available."""

    pulls = [
        AssociatedPull(11, "main", ("semver:patch",)),
        AssociatedPull(10, "dev", ("semver:minor",)),
    ]

    assert resolve_promotion_semver_label(pulls, "dev") == (10, "semver:minor")


def test_resolve_range_preserves_highest_unpromoted_impact() -> None:
    """Keep an earlier major/minor change from being downgraded on refresh."""

    label, numbers = resolve_promotion_range_semver_label(
        [
            [AssociatedPull(20, "dev", ("semver:minor",))],
            [AssociatedPull(21, "dev", ("semver:patch",))],
            [AssociatedPull(20, "dev", ("semver:minor",))],
        ],
        "dev",
    )

    assert label == "semver:minor"
    assert numbers == [20, 21]


def test_resolve_range_rejects_ambiguous_source_pull() -> None:
    """Do not publish when one source PR carries conflicting release labels."""

    with pytest.raises(ValueError, match="at most one"):
        resolve_promotion_range_semver_label(
            [[AssociatedPull(22, "dev", ("semver:patch", "semver:major"))]],
            "dev",
        )


def test_update_version_files_keeps_python_metadata_aligned(tmp_path: Path) -> None:
    """Update pyproject metadata and the public package version together."""

    pyproject = tmp_path / "pyproject.toml"
    package_init = tmp_path / "__init__.py"
    pyproject.write_text('[project]\nname = "example"\nversion = "1.0.1"\n')
    package_init.write_text('__version__ = "1.0.1"\n')

    update_version_files(
        "1.1.0",
        pyproject_path=pyproject,
        init_path=package_init,
    )

    assert read_project_version(pyproject.read_text()) == "1.1.0"
    assert package_init.read_text() == '__version__ = "1.1.0"\n'


def test_update_version_files_rejects_missing_public_version(tmp_path: Path) -> None:
    """Avoid a partial metadata update when the package declaration drifts."""

    pyproject = tmp_path / "pyproject.toml"
    package_init = tmp_path / "__init__.py"
    pyproject.write_text('[project]\nname = "example"\nversion = "1.0.1"\n')
    package_init.write_text("# no version declaration\n")

    with pytest.raises(ValueError, match="exactly one"):
        update_version_files(
            "1.0.2",
            pyproject_path=pyproject,
            init_path=package_init,
        )

    assert read_project_version(pyproject.read_text()) == "1.0.1"
