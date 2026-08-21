#!/usr/bin/env python3
"""Fixture stub for a consuming project's own `scripts/verify.py`, used
only by `tests/test-doc-sync.sh` (ADR-0005,
`docs/adr/0005-ci-pipeline.md`). Implements just enough of the
SPEC.md/CHECKPOINT.md contract `doc_sync.py`'s DETECT/VALIDATE steps
rely on -- source_file, pattern ("checkpoint"/"UNKNOWN"),
structure.status, spec_path -- to drive the scenario suite. Not a copy
of any real project's verify.py: this repository is client-agnostic
and does not ship one (see README, "Scope").

Convention: a SPEC.md at repo root or one level down pairs with a
CHECKPOINT.md in the same directory ("checkpoint" pattern); a SPEC.md
with no such CHECKPOINT.md is "UNKNOWN". Each CHECKPOINT.md
"## <heading>" block must carry "- verify:", "- done-when:", and
"- status:" lines to be structurally OK -- the same rule doc_sync.py's
RECONCILE step (find_checkpoint_missing_fields) independently
re-derives to decide what to fix.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
REQUIRED_FIELD_RES = {
    "verify": re.compile(r"^\s*-\s*verify:", re.MULTILINE),
    "done-when": re.compile(r"^\s*-\s*done-when:", re.MULTILINE),
    "status": re.compile(r"^\s*-\s*status:", re.MULTILINE),
}

NON_COMPONENT_DIRS = {"scripts", ".git", ".tempest"}


def validate_checkpoint(text: str) -> dict:
    headings = list(HEADING_RE.finditer(text))
    if not headings:
        return {"status": "MALFORMED", "missing": ["no '## <heading>' block found"]}
    missing_summary = []
    for i, match in enumerate(headings):
        block_end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        content = text[match.end():block_end]
        missing = [name for name, pat in REQUIRED_FIELD_RES.items() if not pat.search(content)]
        if missing:
            missing_summary.append(f"{match.group(1).strip()}: missing {', '.join(missing)}")
    status = "MALFORMED" if missing_summary else "OK"
    return {"status": status, "missing": missing_summary}


def discover_components(repo_root: Path) -> list[tuple[Path, Path | None]]:
    """Returns (spec_path, checkpoint_path_or_None) pairs: repo-root
    SPEC.md plus any immediate subdirectory's SPEC.md."""
    pairs: list[tuple[Path, Path | None]] = []
    root_spec = repo_root / "SPEC.md"
    if root_spec.is_file():
        checkpoint = repo_root / "CHECKPOINT.md"
        pairs.append((root_spec, checkpoint if checkpoint.is_file() else None))
    for child in sorted(repo_root.iterdir()):
        if not child.is_dir() or child.name in NON_COMPONENT_DIRS or child.name.startswith("."):
            continue
        spec = child / "SPEC.md"
        if spec.is_file():
            checkpoint = child / "CHECKPOINT.md"
            pairs.append((spec, checkpoint if checkpoint.is_file() else None))
    return pairs


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    pairs = discover_components(repo_root)
    if not pairs:
        print("verify: no SPEC.md files found", file=sys.stderr)
        return 2

    results = []
    for spec_path, checkpoint_path in pairs:
        if checkpoint_path is not None:
            structure = validate_checkpoint(checkpoint_path.read_text(encoding="utf-8"))
            results.append({
                "spec_path": str(spec_path),
                "pattern": "checkpoint",
                "source_file": str(checkpoint_path),
                "structure": structure,
            })
        else:
            results.append({
                "spec_path": str(spec_path),
                "pattern": "UNKNOWN",
                "source_file": None,
                "structure": {"status": "UNKNOWN", "missing": []},
            })

    print(json.dumps(results, indent=2, ensure_ascii=False))
    any_not_ok = any(r["structure"]["status"] != "OK" for r in results)
    return 1 if any_not_ok else 0


if __name__ == "__main__":
    sys.exit(main())
