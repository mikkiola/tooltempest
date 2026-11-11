#!/usr/bin/env python3
"""Independent completeness check for MANIFEST.txt, per ADR-0006
(docs/adr/0006-vendor-manifest.md) and ADR-0008
(docs/adr/0008-reference-documentation-category.md). Compares
MANIFEST.txt against `git ls-files` across the six Composition
directories (scripts/, schemas/, skills/, rules/, docs/reference/, docs/tooltempest/) and
exits non-zero on any mismatch in either direction (a file present on
disk but missing from the manifest, or listed in the manifest but no
longer present).

Standalone by design, per ADR-0006: runnable by a developer locally or
as this repository's own local pre-commit hook, with no dependency on
ADR-0005's CI existing.

It has no dependency on any other script of this repository: it finds the
repository root with `git rev-parse --show-toplevel`, so it works regardless
of the caller's working directory.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# The six Composition directories: the four from ADR-0006 ("every
# git-tracked file under scripts/, schemas/, skills/, rules/"), docs/reference/,
# added by ADR-0008 for shared reference documentation, and docs/tooltempest/,
# the root of the consumer contract (ADR-0017).
COMPOSITION_DIRS = ("scripts/", "schemas/", "skills/", "rules/", "docs/reference/", "docs/tooltempest/")

MANIFEST_NAME = "MANIFEST.txt"


def repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    )
    return Path(result.stdout.strip()).resolve()


def tracked_files(root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", *COMPOSITION_DIRS],
        cwd=root, capture_output=True, text=True, check=True,
    )
    return {line for line in result.stdout.splitlines() if line}


def manifest_files(root: Path) -> set[str]:
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.exists():
        print(f"{MANIFEST_NAME} not found at {manifest_path}", file=sys.stderr)
        sys.exit(1)
    return {line.strip() for line in manifest_path.read_text().splitlines() if line.strip()}


def main() -> int:
    root = repo_root()
    tracked = tracked_files(root)
    manifest = manifest_files(root)

    missing_from_manifest = sorted(tracked - manifest)
    missing_from_tree = sorted(manifest - tracked)

    if not missing_from_manifest and not missing_from_tree:
        print(f"OK: {MANIFEST_NAME} matches {len(tracked)} tracked file(s) "
              f"under {', '.join(COMPOSITION_DIRS)}.")
        return 0

    if missing_from_manifest:
        print(f"Tracked but missing from {MANIFEST_NAME}:", file=sys.stderr)
        for path in missing_from_manifest:
            print(f"  {path}", file=sys.stderr)
    if missing_from_tree:
        print(f"Listed in {MANIFEST_NAME} but not git-tracked:", file=sys.stderr)
        for path in missing_from_tree:
            print(f"  {path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
