"""Canonical bundled-schema locations shared by the contract test suites.

The runtime package owns the repository's only checked-in OpenAPI documents.
Tests intentionally read those same files so schema sync cannot leave a second,
test-only copy stale or add hundreds of thousands of duplicated lines.
"""

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "zoom_sdk"
ENDPOINT_SCHEMA_ROOT = PACKAGE_ROOT / "endpoints"
MASTER_ACCOUNT_SCHEMA_ROOT = PACKAGE_ROOT / "master_accounts"
WEBHOOK_SCHEMA_ROOT = PACKAGE_ROOT / "webhooks"
