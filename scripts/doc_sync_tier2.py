#!/usr/bin/env python3
"""Tier 2 /doc-sync diff generation, apply, and CLI entry point. See
ADR-0002 (docs/adr/0002-tier2-doc-sync.md) and ADR-0003
(docs/adr/0003-tier2-confirmation-granularity.md) for the contract this
module implements.

Covers: snapshotting the four milestone-reconciliation target files
(ARCHITECTURE.md, README.md, BACKLOG.md, ROADMAP.md), generating a
line-level diff against proposed content, applying that content to disk
with write behavior branched by document role per ADR-0002(b) and
ADR-0004 -- ARCHITECTURE.md and README.md are written directly,
BACKLOG.md and ROADMAP.md require one per-file human confirmation per
ADR-0003 -- and a CLI (`doc_sync_tier2.py apply --proposed <path>`) the
calling agent invokes once it has, by its own judgment, decided a
milestone is complete. This module contains no milestone-detection
logic of any kind; that judgment happens entirely outside this code,
per ADR-0002(c).

CLI --proposed JSON key format: keys MUST exactly match the TIER2_DOCS
strings exactly -- repo-root-relative paths, "docs/"-prefixed for the
three docs/-folder files (e.g. "docs/ARCHITECTURE.md") and un-prefixed
for README.md, which lives at the repo root. A key outside TIER2_DOCS,
including such near-misses (e.g. "docs/README.md" or bare
"ARCHITECTURE.md"), is rejected outright rather than silently ignored
or fuzzy-matched.

scripts/ has no __init__.py, so it is not an importable package --
this module adds its own directory to sys.path before importing from
doc_sync, rather than relying on a relative import, so it works
regardless of the caller's working directory.
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from doc_sync import relative_to_root, repo_root, restore_snapshots  # noqa: E402

__all__ = [
    "TIER2_DOCS",
    "GATED_DOCS",
    "snapshot_tier2_docs",
    "build_document_diffs",
    "apply_tier2_sync",
    "restore_snapshots",
    "main",
]

# The four Tier 2 target files, per ADR-0002 and ADR-0004.
# CONSTITUTION.md is explicitly out of scope for Tier 2 (ADR-0002,
# Scope / Invariants) and must never be added here. Order is
# load-bearing: apply_tier2_sync() processes files strictly in this
# order, so both direct-write documents (ARCHITECTURE.md, README.md)
# always happen before either gated confirmation. README.md has no
# "docs/" prefix because, unlike the other three, it lives at the
# consuming project's repo root, not under docs/ -- see ADR-0004.
TIER2_DOCS = ("docs/ARCHITECTURE.md", "README.md", "docs/BACKLOG.md", "docs/ROADMAP.md")

# Documents that require human confirmation before a write, per
# ADR-0002(b). Everything in TIER2_DOCS not in GATED_DOCS is
# direct-write (ARCHITECTURE.md and, per ADR-0004, README.md).
GATED_DOCS = frozenset({"docs/BACKLOG.md", "docs/ROADMAP.md"})


def snapshot_tier2_docs(root: Path) -> dict[str, tuple[bool, str | None]]:
    """Takes one invocation-level snapshot covering all four Tier 2
    target files, per ADR-0002(a) ("A snapshot is taken once per
    `/doc-sync` invocation, covering all three target files together" --
    extended to the fourth, README.md, by ADR-0004 on the same terms).

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


def apply_tier2_sync(
    root: Path,
    proposed: dict[str, str],
    interactive: bool = True,
) -> dict:
    """Applies proposed Tier 2 document changes to disk.

    Takes its own invocation-level snapshot (per ADR-0002(a) -- callers
    must not pass in a snapshot from an earlier call) and writes files
    strictly in TIER2_DOCS order: ARCHITECTURE.md, then README.md, then
    BACKLOG.md, then ROADMAP.md -- both direct-write documents always
    precede both gated ones.

    Write behavior branches by document role, per ADR-0002(b) and
    ADR-0004: ARCHITECTURE.md and README.md are written directly, with
    no confirmation. Each of BACKLOG.md and ROADMAP.md requires exactly
    one accept/reject decision covering its entire proposed diff before
    it is written -- per ADR-0003, there is no partial, per-line
    application within a file. Every document's diff is printed before
    its write decision (direct or gated), since visibility is never
    restricted, only the write itself.

    In interactive=True mode (the default), a gated document's
    confirmation is read via input(): only a case-insensitive "y" or
    "yes" counts as acceptance; any other input -- including empty or
    unrecognized text -- is treated as an immediate rejection, with no
    re-prompt.

    A rejection, or any exception raised anywhere in this function
    (including KeyboardInterrupt and EOFError, which do not inherit
    from Exception), rolls back every file this invocation has written
    so far, using restore_snapshots() against this invocation's own
    snapshot -- so no invocation ever leaves a partially-written state
    on disk. An exception is always re-raised after rollback; it is
    never swallowed.

    Args:
      root: Repository root, as returned by doc_sync.repo_root().
      proposed: {relative_path: new_full_text}, keyed like
        snapshot_tier2_docs()'s output. May be a subset of TIER2_DOCS;
        a file absent from `proposed` is left untouched.
      interactive: True prompts for each gated document's confirmation.
        False applies ARCHITECTURE.md and README.md directly without
        prompting, for automated/CI callers that must not block on
        input() -- it is NOT a general bypass for the gated documents:
        if `proposed` contains any change to BACKLOG.md or ROADMAP.md while
        interactive=False, this function raises RuntimeError before
        writing anything. Per ADR-0002(b), gating those two documents
        on human judgment is the decision's entire purpose; a
        non-interactive path that could apply them unreviewed would
        defeat it outright.

    Returns:
      {
        "written": [relative_path, ...],   # in TIER2_DOCS order
        "rejected": [relative_path, ...],  # empty unless a gated
                                            # document was rejected
        "rolled_back": bool,
        "status": "applied" | "rejected" | "no_changes",
      }
      On rejection, "written" and "rejected" describe the pre-rollback
      state (what had been written before the rejection, and which
      document triggered it) -- callers must not treat a non-empty
      "written" list together with "rolled_back": True as content still
      present on disk.

    Raises:
      RuntimeError: interactive=False and `proposed` contains a change
        to a document in GATED_DOCS.
      Exception: any exception encountered while writing or prompting,
        re-raised after this invocation's writes are rolled back.
    """
    if not interactive:
        blocked = [rel for rel in TIER2_DOCS if rel in GATED_DOCS and rel in proposed]
        if blocked:
            raise RuntimeError(
                "apply_tier2_sync(interactive=False) cannot apply changes to "
                f"gated document(s) {blocked}: BACKLOG.md and ROADMAP.md "
                "require human confirmation per ADR-0002(b). Non-interactive "
                "mode is restricted to ARCHITECTURE.md; call with "
                "interactive=True to apply changes to gated documents."
            )

    snapshots = snapshot_tier2_docs(root)
    diffs = build_document_diffs(snapshots, proposed)

    written: list[str] = []
    rejected: list[str] = []
    rolled_back = False

    def roll_back() -> None:
        nonlocal rolled_back
        if not rolled_back:
            restore_snapshots(root, snapshots, written)
            rolled_back = True

    try:
        for rel in TIER2_DOCS:
            if rel not in proposed:
                continue

            diff_text = "".join(diffs[rel])
            print(f"--- proposed changes: {rel} ---")
            print(diff_text if diff_text else "(no changes)")

            if not diff_text:
                continue  # Proposed content matches the snapshot; nothing to write.

            if rel in GATED_DOCS and interactive:
                answer = input(f"Apply changes to {rel}? [y/N] ")
                if answer.strip().lower() not in ("y", "yes"):
                    rejected.append(rel)
                    roll_back()
                    return {
                        "written": written,
                        "rejected": rejected,
                        "rolled_back": rolled_back,
                        "status": "rejected",
                    }

            (root / rel).write_text(proposed[rel], encoding="utf-8")
            written.append(rel)
    except BaseException:
        roll_back()
        raise

    return {
        "written": written,
        "rejected": rejected,
        "rolled_back": rolled_back,
        "status": "applied" if written else "no_changes",
    }


def _load_proposed(proposed_arg: str, non_interactive: bool) -> dict[str, str]:
    """Loads and validates the CLI's --proposed JSON payload.

    Reads no gated document into memory beyond what validation requires
    and touches no Tier 2 target file -- all failures here happen
    before apply_tier2_sync() is ever called.

    Args:
      proposed_arg: A file path, or "-" to read the JSON from stdin.
      non_interactive: Whether --non-interactive was passed. Required
        to be True when proposed_arg is "-", since apply_tier2_sync()'s
        interactive confirmations also read from stdin; reading the
        proposed-content JSON from that same stream would collide with
        them.

    Returns:
      {relative_path: new_full_text}, validated to contain only keys
      from TIER2_DOCS mapped to string values.

    Raises:
      ValueError: proposed_arg is "-" without non_interactive, the JSON
        top level isn't an object, a key falls outside TIER2_DOCS, or a
        value isn't a string.
      FileNotFoundError: proposed_arg names a path that doesn't exist.
      json.JSONDecodeError: the loaded content isn't valid JSON.
    """
    if proposed_arg == "-":
        if not non_interactive:
            raise ValueError(
                '--proposed - (stdin) requires --non-interactive: '
                "apply_tier2_sync()'s interactive mode reads confirmations "
                "from stdin, so reading the proposed-content JSON from the "
                "same stream would collide with those prompts."
            )
        raw = sys.stdin.read()
    else:
        raw = Path(proposed_arg).read_text(encoding="utf-8")

    data = json.loads(raw)

    if not isinstance(data, dict):
        raise ValueError(
            "--proposed JSON must be an object of {relative_path: "
            f"new_full_text}}; got a top-level {type(data).__name__}."
        )

    valid_keys = set(TIER2_DOCS)
    bad_keys = sorted(key for key in data if key not in valid_keys)
    if bad_keys:
        raise ValueError(
            f"--proposed JSON contains key(s) outside TIER2_DOCS: {bad_keys}. "
            f"Valid keys are exactly: {list(TIER2_DOCS)} -- repo-root-relative "
            'paths with the "docs/" prefix (e.g. "docs/ARCHITECTURE.md", not '
            '"ARCHITECTURE.md").'
        )

    bad_values = sorted(key for key, value in data.items() if not isinstance(value, str))
    if bad_values:
        raise ValueError(
            f"--proposed JSON value(s) for key(s) {bad_values} must be "
            "strings (the full proposed file content)."
        )

    return data


def cmd_apply(root: Path, proposed_arg: str, non_interactive: bool) -> int:
    """Implements the `apply` CLI command: load --proposed content, call
    apply_tier2_sync(), and report the result.

    Returns:
      0 if apply_tier2_sync() reports status "applied"; 1 otherwise
      (invalid --proposed input, a rejected/rolled-back run, or the
      RuntimeError apply_tier2_sync() raises for a gated document in
      --non-interactive mode).
    """
    try:
        proposed = _load_proposed(proposed_arg, non_interactive)
    except FileNotFoundError as exc:
        print(
            f"[doc_sync_tier2 apply] FAIL: --proposed path not found: {exc}",
            file=sys.stderr,
        )
        return 1
    except json.JSONDecodeError as exc:
        print(
            f"[doc_sync_tier2 apply] FAIL: --proposed content is not valid JSON: {exc}",
            file=sys.stderr,
        )
        return 1
    except ValueError as exc:
        print(f"[doc_sync_tier2 apply] FAIL: {exc}", file=sys.stderr)
        return 1

    try:
        result = apply_tier2_sync(root, proposed, interactive=not non_interactive)
    except RuntimeError as exc:
        print(f"[doc_sync_tier2 apply] FAIL: {exc}", file=sys.stderr)
        return 1

    print(f"[doc_sync_tier2 apply] status: {result['status']}")
    print(f"[doc_sync_tier2 apply] written: {result['written']}")
    print(f"[doc_sync_tier2 apply] rejected: {result['rejected']}")
    print(f"[doc_sync_tier2 apply] rolled_back: {result['rolled_back']}")
    return 0 if result["status"] == "applied" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-p",
        "--proposed",
        required=True,
        help=(
            'Path to a JSON file of {"relative_path": "new_full_text"} for '
            "one or more of the four Tier 2 documents. Keys MUST exactly "
            'match TIER2_DOCS -- repo-root-relative paths, "docs/"-prefixed '
            'for the docs/-folder files (e.g. "docs/ARCHITECTURE.md") and '
            'un-prefixed for README.md (repo root). A key omitted from the '
            'JSON is left untouched; a key outside TIER2_DOCS -- including '
            'a near-miss like "docs/README.md" -- is rejected. Pass "-" to '
            "read the JSON from stdin instead of a file -- this requires "
            "--non-interactive, since apply_tier2_sync()'s interactive "
            "confirmations also read from stdin."
        ),
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help=(
            "Apply ARCHITECTURE.md and README.md directly without "
            "prompting. Any proposed change to BACKLOG.md or ROADMAP.md "
            "is rejected with an error before anything is written -- per "
            "ADR-0002(b), this mode is not a bypass for the gated "
            "documents."
        ),
    )
    args = parser.parse_args()
    root = repo_root()
    return cmd_apply(root, args.proposed, args.non_interactive)


if __name__ == "__main__":
    sys.exit(main())
