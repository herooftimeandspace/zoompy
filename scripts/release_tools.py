"""Deterministic semantic-version helpers for promotion and release workflows.

This module deliberately keeps GitHub lookups, Git history inspection, version
calculation, and Python metadata updates behind explicit command-line
subcommands. Workflows can therefore reuse tested Python logic instead of
embedding fragile release decisions in shell expressions.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VALID_SEMVER_LABELS = ("semver:patch", "semver:minor", "semver:major")
SEMVER_LABEL_IMPACT = {label: index for index, label in enumerate(VALID_SEMVER_LABELS)}
VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
INIT_VERSION_PATTERN = re.compile(r'^__version__ = "[^"]+"$', re.MULTILINE)


@dataclass(frozen=True)
class AssociatedPull:
    """Minimal associated-pull data needed to resolve a promotion label."""

    number: int
    base_ref: str
    labels: tuple[str, ...]


def bump_version(current_version: str, part: str) -> str:
    """Increment one stable semantic-version component."""

    match = VERSION_PATTERN.fullmatch(current_version.strip())
    if match is None:
        raise ValueError(f"Unsupported version format: {current_version!r}.")
    major, minor, patch = (int(value) for value in match.groups())
    if part == "major":
        major, minor, patch = major + 1, 0, 0
    elif part == "minor":
        minor, patch = minor + 1, 0
    elif part == "patch":
        patch += 1
    else:
        raise ValueError(f"Unsupported semantic-version part: {part!r}.")
    return f"{major}.{minor}.{patch}"


def select_semver_label(
    labels: tuple[str, ...] | list[str],
    default_label: str = "semver:patch",
) -> str:
    """Return one unambiguous release label, defaulting safely to patch."""

    if default_label not in VALID_SEMVER_LABELS:
        raise ValueError(f"Unsupported default semantic-version label: {default_label!r}.")
    selected = tuple(dict.fromkeys(label for label in labels if label in VALID_SEMVER_LABELS))
    if not selected:
        return default_label
    if len(selected) == 1:
        return selected[0]
    raise ValueError(
        "Expected at most one semantic-version label, found " + ", ".join(selected) + "."
    )


def preferred_associated_pull(
    pulls: list[AssociatedPull],
    base_ref: str,
) -> AssociatedPull | None:
    """Prefer the source PR targeting ``base_ref`` over later promotion PRs."""

    if base_ref:
        for pull in pulls:
            if pull.base_ref == base_ref:
                return pull
    return pulls[0] if pulls else None


def resolve_promotion_semver_label(
    pulls: list[AssociatedPull],
    base_ref: str,
    default_label: str = "semver:patch",
) -> tuple[int | None, str]:
    """Resolve the label from the preferred source PR for one commit."""

    selected = preferred_associated_pull(pulls, base_ref)
    if selected is None:
        return None, select_semver_label([], default_label)
    return selected.number, select_semver_label(selected.labels, default_label)


def resolve_promotion_range_semver_label(
    commit_pulls: list[list[AssociatedPull]],
    source_base_ref: str,
    default_label: str = "semver:patch",
) -> tuple[str, list[int]]:
    """Resolve the highest release impact across an unpromoted commit range."""

    resolved = select_semver_label([], default_label)
    seen: set[int] = set()
    numbers: list[int] = []
    for pulls in commit_pulls:
        selected = preferred_associated_pull(pulls, source_base_ref)
        if selected is None or selected.number in seen:
            continue
        seen.add(selected.number)
        numbers.append(selected.number)
        label = select_semver_label(selected.labels, default_label)
        if SEMVER_LABEL_IMPACT[label] > SEMVER_LABEL_IMPACT[resolved]:
            resolved = label
    return resolved, numbers


def read_project_version(pyproject_text: str) -> str:
    """Read the normalized project version from one ``pyproject.toml`` body."""

    payload = tomllib.loads(pyproject_text)
    project = payload.get("project")
    version = project.get("version") if isinstance(project, dict) else None
    if not isinstance(version, str) or VERSION_PATTERN.fullmatch(version) is None:
        raise ValueError("pyproject.toml must declare a stable project.version.")
    return version


def update_version_files(
    version: str,
    *,
    pyproject_path: Path,
    init_path: Path,
) -> None:
    """Update Python package metadata through one fail-closed operation."""

    if VERSION_PATTERN.fullmatch(version) is None or version.startswith("v"):
        raise ValueError(f"Version must be an unprefixed stable semantic version: {version!r}.")

    pyproject_text = pyproject_path.read_text(encoding="utf-8")
    current = read_project_version(pyproject_text)
    updated_pyproject = pyproject_text.replace(
        f'version = "{current}"',
        f'version = "{version}"',
        1,
    )
    if updated_pyproject == pyproject_text:
        raise ValueError("Could not update project.version in pyproject.toml.")

    init_text = init_path.read_text(encoding="utf-8")
    updated_init, replacements = INIT_VERSION_PATTERN.subn(
        f'__version__ = "{version}"',
        init_text,
        count=1,
    )
    if replacements != 1:
        raise ValueError("Could not update exactly one zoom_sdk.__version__ declaration.")

    pyproject_path.write_text(updated_pyproject, encoding="utf-8")
    init_path.write_text(updated_init, encoding="utf-8")


def _run_json(command: list[str]) -> Any:
    """Run one argv-safe command and decode its JSON output."""

    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def associated_pulls(repo: str, sha: str) -> list[AssociatedPull]:
    """Return source pull requests GitHub associates with one commit."""

    payload = _run_json(["gh", "api", f"repos/{repo}/commits/{sha}/pulls"])
    if not isinstance(payload, list):
        raise ValueError("GitHub associated-pull response must be a list.")
    pulls: list[AssociatedPull] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        base = item.get("base")
        labels = item.get("labels")
        number = item.get("number")
        if not isinstance(number, int) or not isinstance(base, dict) or not isinstance(labels, list):
            continue
        pulls.append(
            AssociatedPull(
                number=number,
                base_ref=str(base.get("ref", "")),
                labels=tuple(
                    str(label["name"])
                    for label in labels
                    if isinstance(label, dict) and isinstance(label.get("name"), str)
                ),
            )
        )
    return pulls


def commits_between(base: str, head: str) -> list[str]:
    """Return commits in ``base..head`` in stable oldest-first order."""

    completed = subprocess.run(
        ["git", "rev-list", "--reverse", f"{base}..{head}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.split()


def _build_parser() -> argparse.ArgumentParser:
    """Build the release helper's documented command-line interface."""

    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    bump = subparsers.add_parser("bump")
    bump.add_argument("--part", required=True, choices=("patch", "minor", "major"))
    bump.add_argument("--current-version", required=True)

    read = subparsers.add_parser("read-version")
    read.add_argument("--pyproject", default="pyproject.toml")

    set_version = subparsers.add_parser("set-version")
    set_version.add_argument("--version", required=True)
    set_version.add_argument("--pyproject", default="pyproject.toml")
    set_version.add_argument("--init", default="src/zoom_sdk/__init__.py")

    resolve = subparsers.add_parser("resolve-promotion-semver")
    resolve.add_argument("--repo", required=True)
    resolve.add_argument("--sha", required=True)
    resolve.add_argument("--base-ref", default="")
    resolve.add_argument("--default-label", default="semver:patch")

    resolve_range = subparsers.add_parser("resolve-promotion-range-semver")
    resolve_range.add_argument("--repo", required=True)
    resolve_range.add_argument("--base", required=True)
    resolve_range.add_argument("--head", required=True)
    resolve_range.add_argument("--source-base-ref", default="")
    resolve_range.add_argument("--default-label", default="semver:patch")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Dispatch one release helper subcommand."""

    args = _build_parser().parse_args(argv)
    try:
        if args.command == "bump":
            print(bump_version(args.current_version, args.part))
        elif args.command == "read-version":
            text = sys.stdin.read() if args.pyproject == "-" else Path(args.pyproject).read_text()
            print(read_project_version(text))
        elif args.command == "set-version":
            update_version_files(
                args.version,
                pyproject_path=Path(args.pyproject),
                init_path=Path(args.init),
            )
        elif args.command == "resolve-promotion-semver":
            _, label = resolve_promotion_semver_label(
                associated_pulls(args.repo, args.sha),
                args.base_ref,
                args.default_label,
            )
            print(label)
        elif args.command == "resolve-promotion-range-semver":
            pulls = [
                associated_pulls(args.repo, sha)
                for sha in commits_between(args.base, args.head)
            ]
            label, _ = resolve_promotion_range_semver_label(
                pulls,
                args.source_base_ref,
                args.default_label,
            )
            print(label)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
