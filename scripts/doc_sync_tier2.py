#!/usr/bin/env python3
"""Tier 2 /doc-sync diff generation. See ADR-0002
(docs/adr/0002-tier2-doc-sync.md) for the contract this module
implements.

Stage 2 scope only: snapshot the three milestone-reconciliation target
files (ARCHITECTURE.md, BACKLOG.md, ROADMAP.md) and generate a
line-level diff against proposed content. No confirmation flow, no
write-path branching by document role (per ADR-0002's Implementation
Constraints, that is a later stage's job), and no disk writes of any
kind. Pure: snapshot -> proposed content -> diff.

scripts/ has no __init__.py, so it is not an importable package --
this module adds its own directory to sys.path before importing from
doc_sync, rather than relying on a relative import, so it works
regardless of the caller's working directory.
"""
from __future__ import annotations

import difflib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from doc_sync import relative_to_root, restore_snapshots  # noqa: E402

__all__ = [
    "TIER2_DOCS",
    "snapshot_tier2_docs",
    "build_document_diffs",
    "restore_snapshots",
]

# The three Tier 2 target files, per ADR-0002. CONSTITUTION.md is
# explicitly out of scope for Tier 2 (ADR-0002, Scope / Invariants) and
# must never be added here.
TIER2_DOCS = ("docs/ARCHITECTURE.md", "docs/BACKLOG.md", "docs/ROADMAP.md")


def snapshot_tier2_docs(root: Path) -> dict[str, tuple[bool, str | None]]:
    """Takes one invocation-level snapshot covering all three Tier 2
    target files, per ADR-0002(a) ("A snapshot is taken once per
    `/doc-sync` invocation, covering all three target files together").

    Key format matches doc_sync.py's restore_snapshots() exactly: each
    key is a str, relative to `root`, produced by the same
    relative_to_root() helper Tier 1 uses -- required for
    restore_snapshots() to locate these entries by path.

    Returns:
      {relative_path: (existed: bool, original: str|None)}, one entry
      per file in TIER2_DOCS, regardless of whether the file exists yet.
    """
    snapshots: dict[str, tuple[bool, str | None]] = {}
    for rel in TIER2_DOCS:
        path = root / rel
        key = relative_to_root(root, str(path))
        existed = path.is_file()
        original = path.read_text(encoding="utf-8") if existed else None
        snapshots[key] = (existed, original)
    return snapshots


def build_document_diffs(
    snapshots: dict[str, tuple[bool, str | None]],
    proposed: dict[str, str],
) -> dict[str, list[str]]:
    """Produces a unified line-level diff for each proposed document,
    comparing the invocation-start snapshot against proposed new
    content. Uses the same diff format for every document regardless of
    role (ARCHITECTURE.md vs. BACKLOG.md/ROADMAP.md) -- confirmation
    gating by role is a later stage's concern, not this function's.

    Args:
      snapshots: Output of snapshot_tier2_docs().
      proposed: {relative_path: new_full_text}, keyed the same way as
        `snapshots`.

    Returns:
      {relative_path: unified_diff_lines} for each key in `proposed`.

    Raises:
      KeyError: `proposed` contains a path with no matching snapshot
        entry -- callers must snapshot before diffing.

    Reads and writes no file: both sides of the diff come from
    in-memory arguments only.
    """
    diffs: dict[str, list[str]] = {}
    for rel, new_text in proposed.items():
        if rel not in snapshots:
            raise KeyError(
                f"No snapshot entry for {rel!r}; call snapshot_tier2_docs() "
                "before build_document_diffs()."
            )
        _existed, original = snapshots[rel]
        original_lines = (original or "").splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)
        diffs[rel] = list(
            difflib.unified_diff(
                original_lines,
                new_lines,
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
            )
        )
    return diffs
