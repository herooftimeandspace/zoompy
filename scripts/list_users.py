"""Print the first ten Zoom users in a simple terminal table.

This is a deliberately small production-smoke script for local validation.
It exercises the public `zoompy` client, uses the generated SDK surface, and
prints a readable table without pulling in any third-party table-formatting
dependencies.

Expected environment variables:

* ZOOM_ACCOUNT_ID
* ZOOM_CLIENT_ID
* ZOOM_CLIENT_SECRET

Optional:

* ZOOM_BASE_URL
* ZOOM_OAUTH_URL
* ZOOM_TOKEN_SKEW_SECONDS
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from zoompy import ZoomClient, configure_logging

LOGGER = logging.getLogger("zoompy.scripts.list_users")


def _string_value(value: object) -> str:
    """Return a safe printable string for one table cell.

    Zoom responses occasionally omit optional user fields. Converting missing
    values to an empty string keeps the table output stable and avoids noisy
    `None` text in the terminal.
    """

    if value is None:
        return ""
    return str(value)


def _extract_user_rows(users: Iterable[object]) -> list[tuple[str, str, str, str]]:
    """Extract the four user columns this smoke script cares about.

    The SDK returns typed Pydantic models by default, so attribute access is the
    normal path here. We still use `getattr(..., None)` so the script stays
    tolerant if Zoom omits a field for a specific account or user type.
    """

    rows: list[tuple[str, str, str, str]] = []
    for user in users:
        rows.append(
            (
                _string_value(getattr(user, "first_name", None)),
                _string_value(getattr(user, "last_name", None)),
                _string_value(getattr(user, "display_name", None)),
                _string_value(getattr(user, "email", None)),
            )
        )
    return rows


def _print_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    """Render a plain ASCII table for terminal output.

    Keeping the formatter local and simple makes the script easy to copy into
    another project later, and it avoids adding dependencies just to pretty-
    print a handful of rows.
    """

    widths = [
        max(len(header), *(len(row[index]) for row in rows))
        for index, header in enumerate(headers)
    ]

    def format_row(row: tuple[str, ...]) -> str:
        return " | ".join(
            value.ljust(widths[index]) for index, value in enumerate(row)
        )

    divider = "-+-".join("-" * width for width in widths)
    print(format_row(headers))
    print(divider)
    for row in rows:
        print(format_row(row))


def main() -> None:
    """Fetch the first ten users and print them in a terminal table."""

    configure_logging("WARNING")

    with ZoomClient() as client:
        users = tuple(client.users.list.iter_all(page_size=1000))[:10]

    rows = _extract_user_rows(users)
    if not rows:
        LOGGER.info("No users returned.")
        return

    _print_table(
        headers=("First Name", "Last Name", "Display Name", "Email"),
        rows=rows,
    )


if __name__ == "__main__":
    main()
