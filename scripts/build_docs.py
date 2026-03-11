"""Assemble and build the published documentation site for `zoompy`.

The repository already has strong canonical documentation sources:

* `README.md` for product and usage guidance
* `CHANGELOG.md` for release history
* `CONTRIBUTING.md` and `SECURITY.md` for project process
* docstrings in the Python source tree for API details

This script keeps those sources authoritative while still producing one clean
MkDocs Material site. It copies the root markdown files into the docs tree,
generates HTML API reference with `pdoc`, and leaves MkDocs to build the final
static site.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOCS_ROOT = PROJECT_ROOT / "docs"
GENERATED_ROOT = DOCS_ROOT / "generated"
API_HTML_ROOT = DOCS_ROOT / "api_html"
API_INDEX_PATH = DOCS_ROOT / "api" / "index.md"

ROOT_MARKDOWN_FILES = {
    "README.md": "readme.md",
    "CHANGELOG.md": "changelog.md",
    "CONTRIBUTING.md": "contributing.md",
    "SECURITY.md": "security.md",
}

PDOC_MODULES = (
    "zoompy",
    "zoompy.client",
    "zoompy.schema",
    "zoompy.sdk",
    "zoompy.auth",
    "zoompy.config",
    "zoompy.logging",
)

API_WRAPPERS = (
    ("zoompy", "zoompy.html", "zoompy.md"),
    ("zoompy.client", "zoompy.client.html", "zoompy-client.md"),
    ("zoompy.schema", "zoompy.schema.html", "zoompy-schema.md"),
    ("zoompy.sdk", "zoompy.sdk.html", "zoompy-sdk.md"),
    ("zoompy.auth", "zoompy.auth.html", "zoompy-auth.md"),
    ("zoompy.config", "zoompy.config.html", "zoompy-config.md"),
    ("zoompy.logging", "zoompy.logging.html", "zoompy-logging.md"),
)


def copy_root_markdown() -> None:
    """Mirror the root markdown files into the generated docs tree.

    The docs site should not become a second place contributors must update by
    hand. These copied pages make the published docs and repository browsing
    reflect the same text while keeping the root files as the source of truth.
    """

    GENERATED_ROOT.mkdir(parents=True, exist_ok=True)
    banner = (
        "> This page is generated from a repository root document. Edit the\n"
        "> corresponding root file and rebuild the documentation site.\n\n"
    )

    for source_name, target_name in ROOT_MARKDOWN_FILES.items():
        source_path = PROJECT_ROOT / source_name
        target_path = GENERATED_ROOT / target_name
        content = source_path.read_text(encoding="utf-8")
        content = content.replace("(./CHANGELOG.md)", "(changelog.md)")
        content = content.replace("(./.env.example)", "(env-example.md)")
        target_path.write_text(banner + content, encoding="utf-8")

    env_example_path = PROJECT_ROOT / ".env.example"
    env_example_page = GENERATED_ROOT / "env-example.md"
    env_example_page.write_text(
        "# .env Example\n\n"
        "```dotenv\n"
        f"{env_example_path.read_text(encoding='utf-8').rstrip()}\n"
        "```\n",
        encoding="utf-8",
    )


def write_api_landing_page() -> None:
    """Create the Markdown landing page that links into the pdoc output."""

    API_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# API Reference",
        "",
        "The published docs site combines guides, project documents, and a",
        "generated module-level API reference.",
        "",
        "Use the links below when you want class, method, signature, and",
        "docstring details straight from the code.",
        "",
        "## Generated module reference",
        "",
    ]

    for module_name, _html_name, wrapper_name in API_WRAPPERS:
        label = module_name.replace(".", " / ")
        lines.append(f"- [{label}]({wrapper_name})")

    lines.extend(
        [
            "",
            "## Recommended starting points",
            "",
            "- `zoompy` for top-level exports and package-level usage",
            "- `zoompy.client` for `ZoomClient` request behavior",
            "- `zoompy.sdk` for the dynamic SDK layer and generated method surface",
            "- `zoompy.schema` for response and webhook validation internals",
        ]
    )

    API_INDEX_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_api_wrapper_pages() -> None:
    """Create Markdown wrapper pages for the generated `pdoc` HTML files.

    MkDocs validates links between documentation pages in strict mode. Wrapper
    pages keep the API reference inside the documentation graph while still
    letting `pdoc` own the detailed HTML output.
    """

    api_root = API_INDEX_PATH.parent
    api_root.mkdir(parents=True, exist_ok=True)

    for module_name, html_name, wrapper_name in API_WRAPPERS:
        wrapper_path = api_root / wrapper_name
        wrapper_path.write_text(
            "\n".join(
                [
                    f"# {module_name}",
                    "",
                    "This page fronts the generated API reference emitted by",
                    "`pdoc` during the docs build.",
                    "",
                    "<iframe",
                    f'  src="../api_html/{html_name}"',
                    '  style="width: 100%; height: 80vh; border: 1px solid var(--md-default-fg-color--lightest);"',
                    '  title="Generated API reference"',
                    "></iframe>",
                ]
            )
            + "\n",
            encoding="utf-8",
        )


def build_pdoc_reference() -> None:
    """Generate HTML API reference pages with `pdoc`.

    `pdoc` remains useful here because it extracts API docs directly from the
    live package code and docstrings. MkDocs then treats those HTML files as
    static documentation assets and publishes them alongside the narrative
    Markdown pages.
    """

    if API_HTML_ROOT.exists():
        shutil.rmtree(API_HTML_ROOT)
    API_HTML_ROOT.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pdoc",
            "-d",
            "google",
            "-o",
            str(API_HTML_ROOT),
            *PDOC_MODULES,
        ],
        check=True,
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(PROJECT_ROOT / "src"),
        },
    )


def main() -> None:
    """Build the assembled documentation source tree."""

    copy_root_markdown()
    write_api_landing_page()
    write_api_wrapper_pages()
    build_pdoc_reference()


if __name__ == "__main__":
    main()
