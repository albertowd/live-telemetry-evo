"""Extract one version's section from ``CHANGELOG.md``.

Used by the release workflow: given a version (e.g. ``0.5.0``) it writes the
matching ``## [VERSION]`` section body — without the heading itself — to a
file that ``softprops/action-gh-release`` can read via ``body_path``.

Usage:

    python tools/extract_changelog.py <version> [<output-path>]

Exits 0 even when no matching section is found, writing a placeholder body
so the release isn't blocked by a missing changelog entry. Run interactively
to print to stdout instead of writing a file.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"


def extract(version: str, text: str) -> str | None:
    """Return the body of ``## [<version>] ...`` up to the next ``## [``
    heading or end of file. ``None`` when no section matches."""
    pattern = (
        r"^##\s*\[" + re.escape(version) + r"\][^\n]*\n"
        r"(.*?)"
        r"(?=^##\s*\[|\Z)"
    )
    m = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    if m is None:
        return None
    return m.group(1).strip()


def main(argv: list[str]) -> int:
    if not argv:
        sys.stderr.write("usage: extract_changelog.py <version> [<output>]\n")
        return 2
    version = argv[0].lstrip("v")
    output = Path(argv[1]) if len(argv) > 1 else None

    text = CHANGELOG.read_text(encoding="utf-8") if CHANGELOG.is_file() else ""
    body = extract(version, text)
    if body is None:
        body = f"_No changelog entry for {version}._"

    if output is None:
        sys.stdout.write(body + "\n")
    else:
        output.write_text(body + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
