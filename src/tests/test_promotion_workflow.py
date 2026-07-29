"""Contract tests for credential isolation in the promotion workflow."""

from pathlib import Path

WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "promotion.yml"
)


def _step_block(workflow: str, name: str) -> str:
    """Return one named workflow step through the next sibling step."""

    marker = f"      - name: {name}\n"
    start = workflow.index(marker)
    end = workflow.find("\n      - name:", start + len(marker))
    return workflow[start:] if end == -1 else workflow[start:end]


def test_non_integration_promotion_checks_do_not_receive_zoom_secrets() -> None:
    """Keep unit, security, and release preparation credential-free."""

    workflow = WORKFLOW.read_text(encoding="utf-8")
    block = _step_block(
        workflow,
        "Run and report exact-head non-integration promotion checks",
    )

    assert "id: non_integration_checks" in block
    assert (
        'echo "promotion_sha=${promotion_sha}" >> "${GITHUB_OUTPUT}"'
        in block
    )
    assert "secrets.ZOOM_" not in block
    assert "run_reported_check unit" in block
    assert "run_reported_check security" in block
    assert "run_reported_check release-prep" in block
    assert "run_reported_check integration" not in block


def test_integration_promotion_check_owns_zoom_secret_environment() -> None:
    """Expose Zoom configuration only to the live integration check."""

    workflow = WORKFLOW.read_text(encoding="utf-8")
    block = _step_block(
        workflow,
        "Run and report exact-head integration check",
    )

    assert (
        "if: always() && "
        "steps.non_integration_checks.outputs.promotion_sha != ''"
    ) in block
    assert (
        "PROMOTION_SHA: "
        "${{ steps.non_integration_checks.outputs.promotion_sha }}"
    ) in block
    assert "git checkout \"${PROMOTION_SHA}\"" in block
    for name in (
        "ZOOM_ACCOUNT_ID",
        "ZOOM_CLIENT_ID",
        "ZOOM_CLIENT_SECRET",
        "ZOOM_BASE_URL",
        "ZOOM_OAUTH_URL",
        "ZOOM_TOKEN_SKEW_SECONDS",
    ):
        assert f"{name}: ${{{{ secrets.{name} }}}}" in block


def test_integration_omits_unconfigured_optional_zoom_settings() -> None:
    """Treat empty optional secrets as absent so SDK defaults remain active."""

    workflow = WORKFLOW.read_text(encoding="utf-8")
    block = _step_block(
        workflow,
        "Run and report exact-head integration check",
    )

    assert "if [ -z \"${!optional_name:-}\" ]; then" in block
    assert "unset \"${optional_name}\"" in block
    for name in (
        "ZOOM_BASE_URL",
        "ZOOM_OAUTH_URL",
        "ZOOM_TOKEN_SKEW_SECONDS",
    ):
        assert name in block
