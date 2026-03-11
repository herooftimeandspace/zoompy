"""Generate a Shields.io endpoint badge from a coverage XML report.

This repository uses `pytest-cov` in CI to calculate test coverage and emit a
machine-readable XML summary. Shields endpoint badges consume a small JSON file
with a label, message, and color. This script bridges those two formats so each
tracked branch can publish its own current coverage badge without depending on
an extra external reporting service.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from xml.etree import ElementTree


def coverage_color(percent: float) -> str:
    """Return a badge color that roughly matches common coverage expectations."""

    if percent >= 95.0:
        return "brightgreen"
    if percent >= 90.0:
        return "green"
    if percent >= 80.0:
        return "yellowgreen"
    if percent >= 70.0:
        return "yellow"
    if percent >= 60.0:
        return "orange"
    return "red"


def parse_line_rate(xml_path: Path) -> float:
    """Extract the top-level line-rate percentage from a coverage XML report."""

    root = ElementTree.fromstring(xml_path.read_text(encoding="utf-8"))
    line_rate = root.attrib.get("line-rate")
    if line_rate is None:
        raise ValueError(f"Coverage report {xml_path} does not contain line-rate.")
    return float(line_rate) * 100.0


def build_badge_payload(label: str, percent: float) -> dict[str, object]:
    """Build the JSON payload format expected by Shields endpoint badges."""

    rounded = round(percent, 1)
    if rounded.is_integer():
        message = f"{int(rounded)}%"
    else:
        message = f"{rounded:.1f}%"

    return {
        "schemaVersion": 1,
        "label": label,
        "message": message,
        "color": coverage_color(percent),
    }


def main() -> None:
    """Parse arguments, read coverage XML, and write a badge JSON file."""

    parser = argparse.ArgumentParser(
        description="Write a Shields.io coverage badge from coverage.xml."
    )
    parser.add_argument(
        "--input",
        default="coverage.xml",
        help="Path to the coverage XML report produced by pytest-cov.",
    )
    parser.add_argument(
        "--output",
        default="badges/coverage.json",
        help="Path to the output Shields endpoint JSON file.",
    )
    parser.add_argument(
        "--label",
        default="coverage",
        help="Badge label text.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    percent = parse_line_rate(input_path)
    payload = build_badge_payload(args.label, percent)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
