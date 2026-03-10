"""Manifest-driven Zoom schema sync utility for this repository.

This script replaces the earlier crawling approach with an explicit sync model:
you provide the exact JSON URLs to download, and the script updates the local
schema inventory from those URLs only.

The workflow is intentionally simple and deterministic:

1. Read a JSON manifest of schema URLs.
2. Download those JSON documents.
3. Keep only documents that look like OpenAPI schemas.
4. Match endpoint schemas to local files by `info.title`.
5. Derive companion webhook URLs and store them in a separate webhook tree.
6. Derive optional master-account URLs and store them in a separate tree.
7. Mirror all canonical trees into the test directory structure.

`src/zoompy/endpoints` is the canonical source of truth for ordinary runtime
API response validation. Master-account documents are stored separately under
`src/zoompy/master_accounts` so they can mirror the same product-family layout
without colliding with ordinary endpoint filenames. Webhook documents are
stored separately under `src/zoompy/webhooks` because they use the OpenAPI
`webhooks` section rather than `paths`, and therefore should not be mixed into
the request/response schema registry used by the client.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_CANONICAL_ROOT = Path("src/zoompy/endpoints")
DEFAULT_TEST_ROOT = Path("src/tests/endpoints")
DEFAULT_MASTER_ACCOUNT_ROOT = Path("src/zoompy/master_accounts")
DEFAULT_TEST_MASTER_ACCOUNT_ROOT = Path("src/tests/master_accounts")
DEFAULT_WEBHOOK_ROOT = Path("src/zoompy/webhooks")
DEFAULT_TEST_WEBHOOK_ROOT = Path("src/tests/webhooks")
DEFAULT_CACHE_ROOT = Path(".cache/zoompy-schema-sync")
DEFAULT_MANIFEST_PATH = Path("scripts/schema_urls.json")
USER_AGENT = "zoompy-schema-sync/0.1 (+https://github.com/herooftimeandspace/zoompy)"


@dataclass(frozen=True)
class DownloadedSchema:
    """One downloaded JSON document that appears to be an OpenAPI schema."""

    url: str
    title: str
    payload: dict[str, Any]
    schema_kind: str


@dataclass(frozen=True)
class DownloadFailure:
    """One manifest source that could not be downloaded successfully."""

    url: str
    expected_title: str | None
    reason: str


@dataclass(frozen=True)
class SchemaSource:
    """One manifest entry describing a schema download source.

    The remote URL basename is intentionally not used to decide the local output
    filename. Some Zoom URLs end in generic names such as `endpoints.json`,
    while the downloaded document itself identifies as `Meetings`, `Users`, and
    so on. Local overwrite targets are resolved by schema title instead.
    """

    url: str
    expected_title: str | None = None
    target_title: str | None = None
    schema_kind: str = "endpoint"
    optional: bool = False


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the sync workflow."""

    parser = argparse.ArgumentParser(
        description="Download listed Zoom schema JSON files and mirror them into tests.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST_PATH,
        help="JSON manifest listing the schema URLs to download.",
    )
    parser.add_argument(
        "--canonical-root",
        type=Path,
        default=DEFAULT_CANONICAL_ROOT,
        help="Canonical schema directory used by the library package.",
    )
    parser.add_argument(
        "--test-root",
        type=Path,
        default=DEFAULT_TEST_ROOT,
        help="Mirrored schema directory used by the contract tests.",
    )
    parser.add_argument(
        "--master-account-root",
        type=Path,
        default=DEFAULT_MASTER_ACCOUNT_ROOT,
        help=(
            "Canonical master-account schema directory used for downloaded "
            "master account specs."
        ),
    )
    parser.add_argument(
        "--test-master-account-root",
        type=Path,
        default=DEFAULT_TEST_MASTER_ACCOUNT_ROOT,
        help=(
            "Mirrored master-account schema directory used by tests and "
            "development tooling."
        ),
    )
    parser.add_argument(
        "--webhook-root",
        type=Path,
        default=DEFAULT_WEBHOOK_ROOT,
        help="Canonical webhook schema directory used for downloaded webhook specs.",
    )
    parser.add_argument(
        "--test-webhook-root",
        type=Path,
        default=DEFAULT_TEST_WEBHOOK_ROOT,
        help="Mirrored webhook schema directory used by tests and development tooling.",
    )
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=DEFAULT_CACHE_ROOT,
        help="Local cache directory for unmatched downloaded schemas.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="Per-request timeout in seconds.",
    )
    parser.add_argument(
        "--mirror-only",
        action="store_true",
        help="Skip downloads and only mirror canonical schemas into the test tree.",
    )
    parser.add_argument(
        "--skip-mirror",
        action="store_true",
        help=(
            "Update canonical endpoint, master-account, and webhook schemas "
            "without mirroring them into src/tests."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report planned changes without writing files.",
    )
    return parser.parse_args()


def load_manifest(path: Path) -> list[SchemaSource]:
    """Load the schema URL manifest.

    The manifest format is intentionally small and human-editable:

    ```json
    {
      "urls": [
        "https://developers.zoom.us/api-hub/meetings/methods/endpoints.json",
        {
          "url": "https://developers.zoom.us/api-hub/users/methods/endpoints.json",
          "expected_title": "Users"
        },
        {
          "url": "https://developers.zoom.us/api-hub/iq/methods/endpoints.json",
          "expected_title": "Revenue Accelerator",
          "webhook_expected_title": "Zoom Revenue Accelerator Webhooks",
          "master_account_expected_title": "Revenue Accelerator"
        }
      ]
    }
    ```

    Plain string entries are supported for convenience. Object entries let you
    state the title you expect the remote schema to declare, which is useful
    when the URL path itself does not resemble the local filename.

    Each manifest entry implicitly represents three downloads:

    * the endpoint schema at `.../methods/endpoints.json`
    * the optional companion webhook schema at `.../events/webhooks.json`
    * the optional master-account schema at `.../ma/master.json`
    """

    if not path.exists():
        raise SystemExit(f"Schema manifest not found: {path}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    urls = payload.get("urls")
    if not isinstance(urls, list):
        raise SystemExit(
            f"Schema manifest must contain a top-level 'urls' list: {path}",
        )

    sources: list[SchemaSource] = []
    for entry in urls:
        if isinstance(entry, str) and entry.strip():
            endpoint_source = SchemaSource(url=entry.strip())
            sources.extend(expand_schema_sources(endpoint_source))
            continue

        if isinstance(entry, dict):
            url = entry.get("url")
            expected_title = entry.get("expected_title")
            webhook_expected_title = entry.get("webhook_expected_title")
            master_account_expected_title = entry.get(
                "master_account_expected_title"
            )
            if not isinstance(url, str) or not url.strip():
                raise SystemExit(
                    f"Manifest entry is missing a usable 'url': {entry!r}",
                )
            if expected_title is not None and not isinstance(expected_title, str):
                raise SystemExit(
                    f"'expected_title' must be a string when provided: {entry!r}",
                )
            if (
                webhook_expected_title is not None and
                not isinstance(webhook_expected_title, str)
            ):
                raise SystemExit(
                    f"'webhook_expected_title' must be a string when provided: {entry!r}",
                )
            if (
                master_account_expected_title is not None and
                not isinstance(master_account_expected_title, str)
            ):
                raise SystemExit(
                    "'master_account_expected_title' must be a string when "
                    f"provided: {entry!r}",
                )
            endpoint_source = SchemaSource(
                url=url.strip(),
                expected_title=expected_title.strip() if isinstance(expected_title, str) else None,
                target_title=expected_title.strip() if isinstance(expected_title, str) else None,
            )
            sources.extend(
                expand_schema_sources(
                    endpoint_source,
                    webhook_expected_title=(
                        webhook_expected_title.strip()
                        if isinstance(webhook_expected_title, str)
                        else None
                    ),
                    master_account_expected_title=(
                        master_account_expected_title.strip()
                        if isinstance(master_account_expected_title, str)
                        else None
                    ),
                )
            )

    if not sources:
        raise SystemExit(
            f"Schema manifest contains no URLs to download: {path}",
        )
    return sources


def expand_schema_sources(
    endpoint_source: SchemaSource,
    webhook_expected_title: str | None = None,
    master_account_expected_title: str | None = None,
) -> list[SchemaSource]:
    """Expand one endpoint source into endpoint, webhook, and master downloads."""

    sources = [endpoint_source]
    if endpoint_source.url.endswith("/methods/endpoints.json"):
        webhook_url = endpoint_source.url.replace(
            "/methods/endpoints.json",
            "/events/webhooks.json",
        )
        sources.append(
            SchemaSource(
                url=webhook_url,
                expected_title=webhook_expected_title or endpoint_source.expected_title,
                target_title=endpoint_source.target_title,
                schema_kind="webhook",
                optional=True,
            )
        )
        master_account_url = endpoint_source.url.replace(
            "/methods/endpoints.json",
            "/ma/master.json",
        )
        sources.append(
            SchemaSource(
                url=master_account_url,
                expected_title=(
                    master_account_expected_title or endpoint_source.expected_title
                ),
                target_title=endpoint_source.target_title,
                schema_kind="master_account",
                optional=True,
            )
        )
    return sources


def fetch_json(url: str, timeout: float) -> dict[str, Any] | None:
    """Download one JSON resource and return it if it parses cleanly."""

    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))
    if isinstance(payload, dict):
        return payload
    return None


def looks_like_openapi_spec(payload: dict[str, Any]) -> bool:
    """Return whether one JSON document resembles an OpenAPI schema."""

    title = payload.get("info", {}).get("title")
    return (
        (
            isinstance(payload.get("paths"), dict) or
            isinstance(payload.get("webhooks"), dict)
        )
        and isinstance(title, str)
        and bool(title)
    )


def download_openapi_specs(
    sources: list[SchemaSource],
    timeout: float,
) -> tuple[list[DownloadedSchema], list[DownloadFailure]]:
    """Download listed JSON URLs and keep only OpenAPI-like documents.

    This function is intentionally continue-on-error. Schema refreshes are a
    batch workflow, so it is more useful to report all broken URLs in one run
    than to abort on the first failure and force a slow fix-rerun loop.
    """

    downloaded: list[DownloadedSchema] = []
    failures: list[DownloadFailure] = []
    for source in sources:
        try:
            payload = fetch_json(source.url, timeout)
        except HTTPError as exc:
            failures.append(
                DownloadFailure(
                    url=source.url,
                    expected_title=source.expected_title,
                    reason=f"HTTP {exc.code}",
                )
            )
            if source.optional and exc.code == 404:
                print(f"note: optional schema not published at {source.url}")
            else:
                print(f"warning: failed to download {source.url}: HTTP {exc.code}")
            continue
        except URLError as exc:
            failures.append(
                DownloadFailure(
                    url=source.url,
                    expected_title=source.expected_title,
                    reason=f"URL error: {exc.reason}",
                )
            )
            print(f"warning: failed to download {source.url}: {exc.reason}")
            continue
        except Exception as exc:  # noqa: BLE001
            failures.append(
                DownloadFailure(
                    url=source.url,
                    expected_title=source.expected_title,
                    reason=f"{type(exc).__name__}: {exc}",
                )
            )
            print(f"warning: failed to download {source.url}: {type(exc).__name__}: {exc}")
            continue

        if payload is None or not looks_like_openapi_spec(payload):
            print(f"warning: skipped non-OpenAPI JSON document from {source.url}")
            failures.append(
                DownloadFailure(
                    url=source.url,
                    expected_title=source.expected_title,
                    reason="downloaded JSON did not look like an OpenAPI schema",
                )
            )
            continue

        title = str(payload.get("info", {}).get("title", "")).strip()
        if source.expected_title and title != source.expected_title:
            print(
                "warning: downloaded schema title did not match expected title: "
                f"url={source.url} expected={source.expected_title!r} actual={title!r}",
            )

        downloaded.append(
            DownloadedSchema(
                url=source.url,
                title=source.target_title or title,
                payload=payload,
                schema_kind=source.schema_kind,
            )
        )
    filtered_failures = [
        failure for failure in failures
        if not any(
            source.url == failure.url and source.optional and failure.reason == "HTTP 404"
            for source in sources
        )
    ]
    return downloaded, filtered_failures


def build_local_title_map(canonical_root: Path) -> dict[str, Path]:
    """Map each known schema title to its canonical repository path."""

    title_map: dict[str, Path] = {}
    for schema_path in sorted(canonical_root.rglob("*.json")):
        payload = json.loads(schema_path.read_text(encoding="utf-8"))
        title = payload.get("info", {}).get("title")
        if isinstance(title, str) and title:
            title_map[title] = schema_path
    return title_map


def build_related_target_map(
    endpoint_root: Path,
    related_root: Path,
) -> dict[str, Path]:
    """Map titles to related-schema paths using endpoint relative paths.

    Webhook and master-account specs reuse endpoint titles such as `Meetings`,
    so we cannot target them by title alone without colliding with the ordinary
    endpoint tree. Instead we mirror the existing endpoint directory layout
    under a separate root and reuse the same relative path.
    """

    target_map: dict[str, Path] = {}
    endpoint_title_map = build_local_title_map(endpoint_root)
    for title, endpoint_path in endpoint_title_map.items():
        relative_path = endpoint_path.relative_to(endpoint_root)
        target_map[title] = related_root / relative_path
    return target_map


def write_schema(path: Path, payload: dict[str, Any], dry_run: bool) -> None:
    """Write one schema file in a stable JSON format."""

    text = json.dumps(payload, indent=2, sort_keys=False) + "\n"
    if dry_run:
        print(f"would update {path}")
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_unmatched_download(
    cache_root: Path,
    schema: DownloadedSchema,
    dry_run: bool,
) -> None:
    """Persist unmatched remote downloads for manual inspection.

    Unmatched files are preserved because they may represent newly added Zoom
    schemas that the repository does not yet track.
    """

    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", schema.title or "unknown")
    target = cache_root / "unmatched" / f"{safe_name}.json"
    write_schema(target, schema.payload, dry_run)


def mirror_tree(source_root: Path, target_root: Path, dry_run: bool) -> None:
    """Mirror the canonical schema tree into the test schema tree."""

    source_files = {
        path.relative_to(source_root)
        for path in source_root.rglob("*.json")
    }
    if target_root.exists():
        target_files = {
            path.relative_to(target_root)
            for path in target_root.rglob("*.json")
        }
    else:
        target_files = set()

    for relative_path in sorted(target_files - source_files):
        stale = target_root / relative_path
        if dry_run:
            print(f"would remove stale mirrored schema {stale}")
        else:
            stale.unlink()

    for relative_path in sorted(source_files):
        source = source_root / relative_path
        target = target_root / relative_path
        if dry_run:
            print(f"would mirror {source} -> {target}")
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def update_from_downloads(
    canonical_root: Path,
    master_account_root: Path,
    webhook_root: Path,
    cache_root: Path,
    downloaded_specs: list[DownloadedSchema],
    dry_run: bool,
) -> tuple[int, int]:
    """Apply downloaded specs to known local schemas by title match."""

    endpoint_title_map = build_local_title_map(canonical_root)
    master_account_title_map = build_related_target_map(
        canonical_root,
        master_account_root,
    )
    webhook_title_map = build_related_target_map(canonical_root, webhook_root)
    updated = 0
    unmatched = 0

    for schema in downloaded_specs:
        if schema.schema_kind == "webhook":
            target = webhook_title_map.get(schema.title)
        elif schema.schema_kind == "master_account":
            target = master_account_title_map.get(schema.title)
        else:
            target = endpoint_title_map.get(schema.title)
        if target is None:
            unmatched += 1
            write_unmatched_download(cache_root, schema, dry_run)
            continue

        print(
            "mapping downloaded "
            f"{schema.schema_kind} title {schema.title!r} "
            f"from {schema.url} -> {target}"
        )
        write_schema(target, schema.payload, dry_run)
        updated += 1

    return updated, unmatched


def main() -> int:
    """Run the schema sync workflow."""

    args = parse_args()

    canonical_root = args.canonical_root.resolve()
    test_root = args.test_root.resolve()
    master_account_root = args.master_account_root.resolve()
    test_master_account_root = args.test_master_account_root.resolve()
    webhook_root = args.webhook_root.resolve()
    test_webhook_root = args.test_webhook_root.resolve()
    cache_root = args.cache_root.resolve()

    if not canonical_root.exists():
        raise SystemExit(f"Canonical schema root does not exist: {canonical_root}")

    updated = 0
    unmatched = 0
    failures: list[DownloadFailure] = []

    if not args.mirror_only:
        sources = load_manifest(args.manifest.resolve())
        downloaded_specs, failures = download_openapi_specs(sources, args.timeout)
        print(f"downloaded {len(downloaded_specs)} OpenAPI-like JSON documents")
        updated, unmatched = update_from_downloads(
            canonical_root=canonical_root,
            master_account_root=master_account_root,
            webhook_root=webhook_root,
            cache_root=cache_root,
            downloaded_specs=downloaded_specs,
            dry_run=args.dry_run,
        )

    if not args.skip_mirror:
        mirror_tree(canonical_root, test_root, args.dry_run)
        mirror_tree(master_account_root, test_master_account_root, args.dry_run)
        mirror_tree(webhook_root, test_webhook_root, args.dry_run)

    if failures:
        print("download failures:")
        for failure in failures:
            print(
                f"  - title={failure.expected_title!r} "
                f"url={failure.url} reason={failure.reason}"
            )

    print(
        f"sync complete: updated={updated} unmatched={unmatched} "
        f"failed={len(failures)} "
        f"mirrored={'no' if args.skip_mirror else 'yes'}",
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
