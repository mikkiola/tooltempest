#!/usr/bin/env python3
"""Tier 1 contract stub for a consuming project's own `scripts/verify.py`.

Used only by `tests/test-doc-sync.sh` (ADR-0005), which drives the legacy
`scripts/doc_sync.py` hook entry points. It stands in for the consumer-side
script that `doc_sync.py` runs, and implements just the output contract
`doc_sync.py` reads: a JSON list of entries with `spec_path`, `pattern`
("inline_spec" or "UNKNOWN"), `source_file` and `structure.status`
("OK", "MALFORMED" or "UNKNOWN"), and the exit codes 0 (all OK), 1 (any
entry not OK) and 2 (no SPEC.md found). Convention modelled: a SPEC.md at
the repository root or one directory down whose `## Milestones` section has
checkbox lines is "inline_spec" (a checkbox line without description text
is MALFORMED); a SPEC.md without such a section is "UNKNOWN". The retired
CHECKPOINT.md pairing is not modelled (ADR-0010).

This is NOT the production SPEC parser. That is `scripts/docops_core/`,
tested through `tests/docops` (which also checks that the SPEC fixtures
next to this file get the verdicts this stub relies on). Nothing here
verifies the production parser, and a pass of `tests/docops` says nothing
about `doc_sync.py`: the two suites cover different code on purpose.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MILESTONES_HEADING_RE = re.compile(r"^##\s+Milestones\b", re.MULTILINE)
NEXT_HEADING_RE = re.compile(r"^##\s+\S", re.MULTILINE)
CHECKBOX_LINE_RE = re.compile(r"^[ \t]*(?:-|\d+\.)\s*\[[ xX]\](.*)$", re.MULTILINE)

NON_COMPONENT_DIRS = {"scripts", ".git", ".tempest"}


def milestone_lines(text: str) -> list[tuple[int, str]]:
    """(line number, description) of each checkbox line in the `## Milestones` section."""
    heading = MILESTONES_HEADING_RE.search(text)
    if heading is None:
        return []
    following = NEXT_HEADING_RE.search(text, heading.end())
    section_end = following.start() if following else len(text)
    found = []
    for match in CHECKBOX_LINE_RE.finditer(text, heading.end(), section_end):
        line_number = text.count("\n", 0, match.start()) + 1
        found.append((line_number, match.group(1).strip()))
    return found


def classify(text: str) -> tuple[str, dict]:
    lines = milestone_lines(text)
    if not lines:
        return "UNKNOWN", {"status": "UNKNOWN", "total_units": 0, "well_formed": 0, "malformed_details": []}
    bad = [{"line": number, "reason": "checkbox line without description text"}
           for number, description in lines if not description]
    return "inline_spec", {"status": "MALFORMED" if bad else "OK", "total_units": len(lines),
                           "well_formed": len(lines) - len(bad), "malformed_details": bad}


def find_spec_files(repo_root: Path) -> list[Path]:
    """SPEC.md at the root, plus SPEC.md directly inside any immediate subdirectory."""
    found = []
    if (repo_root / "SPEC.md").is_file():
        found.append(repo_root / "SPEC.md")
    for child in sorted(repo_root.iterdir()):
        if not child.is_dir() or child.name in NON_COMPONENT_DIRS or child.name.startswith("."):
            continue
        if (child / "SPEC.md").is_file():
            found.append(child / "SPEC.md")
    return found


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    specs = find_spec_files(repo_root)
    if not specs:
        print("verify: no SPEC.md files found", file=sys.stderr)
        return 2
    results = []
    for spec in specs:
        pattern, structure = classify(spec.read_text(encoding="utf-8"))
        results.append({"spec_path": str(spec), "pattern": pattern,
                        "source_file": str(spec) if pattern == "inline_spec" else None,
                        "structure": structure})
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 1 if any(r["structure"]["status"] != "OK" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
