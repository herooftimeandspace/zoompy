"""Regression tests for the repository's single-source schema layout."""

from _schema_roots import (
    ENDPOINT_SCHEMA_ROOT,
    MASTER_ACCOUNT_SCHEMA_ROOT,
    PACKAGE_ROOT,
    WEBHOOK_SCHEMA_ROOT,
)


def test_contract_suites_use_canonical_package_schema_roots() -> None:
    """Keep every contract suite pointed at the runtime package's schemas."""

    roots = (
        ENDPOINT_SCHEMA_ROOT,
        MASTER_ACCOUNT_SCHEMA_ROOT,
        WEBHOOK_SCHEMA_ROOT,
    )

    assert all(root.parent == PACKAGE_ROOT for root in roots)
    assert all(root.is_dir() for root in roots)
    assert all(any(root.rglob("*.json")) for root in roots)
