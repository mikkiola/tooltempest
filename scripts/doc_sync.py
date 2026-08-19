#!/usr/bin/env python3
"""DocOps Protocol runtime. See ADR-0001 (docs/adr/0001-docops-protocol.md)
for the full contract this module implements.

Two entry points, invoked from a consuming project's local git hooks:

  doc_sync.py pre-commit   DETECT -> RECONCILE -> VALIDATE -> RECORD -> STAGE
  doc_sync.py pre-push     hard validation only (runs scripts/verify.py)

Client-agnostic: this module knows only the SPEC.md/CHECKPOINT.md
convention scripts/verify.py (a project-local script, not part of
ToolTempest) already validates structurally. It carries no project- or
domain-specific logic of any kind.

Design notes that are load-bearing, not incidental (see ADR-0001 for
the full reasoning behind each):

- RECONCILE is structural-only in this version: it fills a CHECKPOINT.md
  block's missing `verify:`/`done-when:`/`status:` field with a literal
  `TODO` placeholder. It never invents a field's *value*, and it never
  auto-fixes an inline `## Milestones` checkbox with an empty
  description -- that would require semantic judgment this version does
  not make.
- SNAPSHOT-BEFORE-MODIFY restores from an in-process snapshot, never via
  `git checkout --`, so a human's unstaged edits that predate this run
  are never destroyed by a VALIDATE failure.
- The execution record written by RECORD has no commit_sha field: the
  commit does not exist yet when RECORD runs.
- STAGE (`git add`) runs exactly once, strictly after VALIDATE has
  already succeeded, and never anywhere else in this module.
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROTOCOL_VERSION = "1.0.0"

CHECKPOINT_BLOCK_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
CHECKPOINT_REQUIRED_FIELD_RES = {
    "verify": re.compile(r"^\s*-\s*verify:", re.MULTILINE),
    "done-when": re.compile(r"^\s*-\s*done-when:", re.MULTILINE),
    "status": re.compile(r"^\s*-\s*status:", re.MULTILINE),
}
REQUIRED_FIELD_ORDER = ("verify", "done-when", "status")


def repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    )
    return Path(result.stdout.strip()).resolve()


def run_verify(root: Path) -> tuple[int, list | None, str]:
    """Runs scripts/verify.py and returns (exit_code, parsed_json_or_None,
    stderr_text). Treated as the single source of structural truth for
    both DETECT and VALIDATE, per ADR-0001. A non-zero exit code alone
    does not mean failure here -- scripts/verify.py exits 1 whenever any
    discovered file is MALFORMED, which is the expected, normal state
    for DETECT to observe before RECONCILE has run. Callers decide what
    a given exit code means for their own step."""
    verify_path = root / "scripts" / "verify.py"
    if not verify_path.is_file():
        return 1, None, f"scripts/verify.py not found at {verify_path}"
    proc = subprocess.run(
        [sys.executable, str(verify_path)],
        cwd=root, capture_output=True, text=True,
    )
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError:
        parsed = None
    return proc.returncode, parsed, proc.stderr


def staged_files(root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=root, capture_output=True, text=True, check=True,
    )
    return {line for line in result.stdout.splitlines() if line}


def unstaged_modified_files(root: Path) -> set[str]:
    """Files with a working-tree edit not yet reflected in the index --
    i.e. `git diff` (no --cached). Used together with staged_files() to
    tell an ordinary `git add && git commit` (staged == working tree,
    safe for RECONCILE to fix-and-restage) apart from a genuine partial
    staging (index and working tree diverge -- fixing the working tree
    and restaging it would silently discard whatever the human staged)."""
    result = subprocess.run(
        ["git", "diff", "--name-only"],
        cwd=root, capture_output=True, text=True, check=True,
    )
    return {line for line in result.stdout.splitlines() if line}


def staged_blob_text(root: Path, relative_path: str) -> str | None:
    """Returns the staged (git index) content of a path, or None if it
    is not staged or cannot be read as text."""
    proc = subprocess.run(
        ["git", "show", f":{relative_path}"],
        cwd=root, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def relative_to_root(root: Path, absolute_path: str) -> str:
    return str(Path(absolute_path).resolve().relative_to(root))


def find_checkpoint_missing_fields(text: str) -> list[dict]:
    """Independently re-derives, from raw file text, which '## <heading>'
    blocks are missing which required field -- applying the same
    structural rule scripts/verify.py uses. Returns a list of
    {"heading", "insert_at", "missing"} with insert_at as the absolute
    offset immediately after the heading's own line, so a fix can be
    inserted without re-locating the block by name (heading text is not
    guaranteed unique within a file)."""
    headings = list(CHECKPOINT_BLOCK_HEADING_RE.finditer(text))
    findings = []
    for i, match in enumerate(headings):
        block_end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        content = text[match.end():block_end]
        missing = [
            name for name in REQUIRED_FIELD_ORDER
            if not CHECKPOINT_REQUIRED_FIELD_RES[name].search(content)
        ]
        if not missing:
            continue
        insert_at = match.end()
        if insert_at < len(text) and text[insert_at] == "\n":
            insert_at += 1
        findings.append({
            "heading": match.group(1).strip(),
            "insert_at": insert_at,
            "missing": missing,
        })
    return findings


def apply_checkpoint_fix(text: str, findings: list[dict]) -> str:
    """Inserts a `- <field>: TODO` stub line for each missing field,
    directly after its block's heading line. Existing fields and all
    other content are left untouched. Findings are applied in reverse
    offset order so an earlier insertion never shifts a later one."""
    for finding in sorted(findings, key=lambda f: f["insert_at"], reverse=True):
        stub = "".join(f"- {name}: TODO\n" for name in finding["missing"])
        pos = finding["insert_at"]
        text = text[:pos] + stub + text[pos:]
    return text


def reconcile(root: Path, verify_results: list) -> dict:
    """Structural-only RECONCILE over CHECKPOINT.md files. Never touches
    inline `## Milestones` findings (no safe auto-fix exists for those).

    Returns:
      {
        "blocked": [relative_path, ...],   # non-empty means HARD BLOCK
        "snapshots": {relative_path: (existed: bool, original: str|None)},
        "modified": [relative_path, ...],
        "scanned": int, "affected": int, "updated": int,
      }
    When `blocked` is non-empty, no file has been touched -- the caller
    must stop before applying anything.
    """
    staged = staged_files(root)
    unstaged = unstaged_modified_files(root)

    scanned = 0
    affected = 0
    checkpoint_candidates: list[str] = []
    for entry in verify_results:
        source_file = entry.get("source_file")
        if not source_file:
            continue
        scanned += 1
        structure = entry.get("structure", {})
        if structure.get("status") != "MALFORMED":
            continue
        affected += 1
        if entry.get("pattern") == "checkpoint":
            checkpoint_candidates.append(source_file)

    blocked: list[str] = []
    to_fix: list[str] = []
    for source_file in checkpoint_candidates:
        rel = relative_to_root(root, source_file)
        # A plain `git add <path>` followed immediately by `git commit`
        # (the ordinary workflow) leaves the index identical to the
        # working tree -- rel in staged but NOT in unstaged. That is
        # safe for RECONCILE: fixing the working tree and restaging it
        # only replaces the staged blob with an updated copy of the same
        # content, nothing is discarded. A genuine conflict requires the
        # index and working tree to actually diverge (rel in BOTH staged
        # and unstaged) -- a partial/mid-edit staging, where fixing and
        # restaging the working tree would silently overwrite whatever
        # the human deliberately staged with something they never saw.
        if rel in staged and rel in unstaged:
            staged_text = staged_blob_text(root, rel)
            if staged_text is not None and find_checkpoint_missing_fields(staged_text):
                blocked.append(rel)
            # Staged version is already well-formed (or unreadable as
            # text): the human already resolved it. Leave both the index
            # and the further working-tree edit alone.
            continue
        to_fix.append(rel)

    if blocked:
        return {
            "blocked": blocked, "snapshots": {}, "modified": [],
            "scanned": scanned, "affected": affected, "updated": 0,
        }

    snapshots: dict[str, tuple[bool, str | None]] = {}
    modified: list[str] = []
    for rel in to_fix:
        path = root / rel
        existed = path.is_file()
        original = path.read_text(encoding="utf-8") if existed else None
        findings = find_checkpoint_missing_fields(original or "")
        if not findings:
            continue
        snapshots[rel] = (existed, original)
        path.write_text(apply_checkpoint_fix(original, findings), encoding="utf-8")
        modified.append(rel)

    return {
        "blocked": [], "snapshots": snapshots, "modified": modified,
        "scanned": scanned, "affected": affected, "updated": len(modified),
    }


def restore_snapshots(root: Path, snapshots: dict, modified: list[str]) -> None:
    """SNAPSHOT-BEFORE-MODIFY restore: rewrites each modified file back to
    its exact pre-RECONCILE bytes, or deletes it if it did not exist
    before RECONCILE touched it. Deliberately not `git checkout --`,
    which would instead restore the last committed version and destroy
    any unstaged human edits that predate this run."""
    for rel in modified:
        existed, original = snapshots[rel]
        path = root / rel
        if existed:
            path.write_text(original, encoding="utf-8")
        elif path.exists():
            path.unlink()


def make_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"docops_{ts}_{secrets.token_hex(3)}"


def write_record(
    root: Path, run_id: str, hook: str, started_at: str, finished_at: str,
    total_duration_sec: float, steps: list[str], step_durations_ms: dict,
    counters: dict, timeline_summary: str,
) -> Path:
    record = {
        "run_id": run_id,
        "protocol_version": PROTOCOL_VERSION,
        "hook": hook,
        "started_at": started_at,
        "finished_at": finished_at,
        "total_duration_sec": round(total_duration_sec, 3),
        "steps": steps,
        "step_durations_ms": step_durations_ms,
        "counters": counters,
        "token_usage": {"calls": 0, "input_tokens": 0, "output_tokens": 0},
        "timeline_summary": timeline_summary,
        "result": "SUCCESS",
    }
    runs_dir = root / ".tempest" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    out_path = runs_dir / f"{run_id}.json"
    out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out_path


def cmd_pre_commit(root: Path) -> int:
    start_wall = datetime.now(timezone.utc)
    start_perf = time.monotonic()
    steps: list[str] = []
    step_durations_ms: dict[str, int] = {}

    t0 = time.monotonic()
    steps.append("detect")
    exit_code, verify_results, verify_stderr = run_verify(root)
    step_durations_ms["detect"] = int((time.monotonic() - t0) * 1000)

    if verify_results is None:
        if exit_code == 2:
            print("[doc_sync pre-commit] OK: no SPEC.md files found in this project. Nothing to do.")
            return 0
        print(
            "[doc_sync pre-commit] FAIL: could not read structural discovery from scripts/verify.py.",
            file=sys.stderr,
        )
        if verify_stderr:
            print(verify_stderr, file=sys.stderr)
        return 1

    doc_owned = [e for e in verify_results if e.get("source_file")]
    if not doc_owned or not any(e["structure"].get("status") == "MALFORMED" for e in doc_owned):
        print(
            f"[doc_sync pre-commit] OK: {len(doc_owned)} doc-owned file(s) scanned, "
            "all structurally OK. Nothing to reconcile."
        )
        return 0

    t0 = time.monotonic()
    steps.append("reconcile")
    result = reconcile(root, verify_results)
    step_durations_ms["reconcile"] = int((time.monotonic() - t0) * 1000)

    if result["blocked"]:
        print(
            "[doc_sync pre-commit] FAIL: staged doc-owned file(s) are already structurally "
            "malformed:",
            file=sys.stderr,
        )
        for rel in result["blocked"]:
            print(f"  - {rel}", file=sys.stderr)
        print(
            "[doc_sync pre-commit] Resolve manually (fix the staged content, or "
            "`git restore --staged <path>` to let DocOps reconcile it) and retry.",
            file=sys.stderr,
        )
        return 1

    t0 = time.monotonic()
    steps.append("validate")
    validate_exit, _validate_results, validate_stderr = run_verify(root)
    step_durations_ms["validate"] = int((time.monotonic() - t0) * 1000)

    if validate_exit != 0:
        restore_snapshots(root, result["snapshots"], result["modified"])
        print(
            "[doc_sync pre-commit] FAIL: scripts/verify.py did not pass after RECONCILE. "
            "Restored modified file(s) to their pre-RECONCILE snapshot.",
            file=sys.stderr,
        )
        if validate_stderr:
            print(validate_stderr, file=sys.stderr)
        return 1

    t0 = time.monotonic()
    steps.append("record")
    finished_wall = datetime.now(timezone.utc)
    total_duration_sec = time.monotonic() - start_perf
    counters = {
        "scanned": result["scanned"], "affected": result["affected"], "updated": result["updated"],
    }
    timeline_summary = (
        f"Scanned {result['scanned']} doc-owned file(s); {result['affected']} structurally "
        f"malformed; {result['updated']} reconciled (missing CHECKPOINT field(s) filled with "
        "TODO); validate passed after reconcile."
    )
    run_id = make_run_id()
    record_path = write_record(
        root, run_id, "pre-commit", start_wall.isoformat(), finished_wall.isoformat(),
        total_duration_sec, steps, step_durations_ms, counters, timeline_summary,
    )
    step_durations_ms["record"] = int((time.monotonic() - t0) * 1000)

    git_add_paths = result["modified"] + [str(record_path.relative_to(root))]
    subprocess.run(["git", "add", *git_add_paths], cwd=root, check=True)

    print(
        f"[doc_sync pre-commit] OK: reconciled {result['updated']} file(s); staged them plus "
        f"{record_path.relative_to(root)}."
    )
    return 0


def cmd_pre_push(root: Path) -> int:
    """Hard validation only: runs scripts/verify.py and requires exit
    code 0. Never modifies the working tree, never stages anything,
    never writes a git note, never makes a network call."""
    exit_code, _results, stderr_text = run_verify(root)
    if exit_code != 0:
        print("[doc_sync pre-push] FAIL: scripts/verify.py did not pass.", file=sys.stderr)
        if stderr_text:
            print(stderr_text, file=sys.stderr)
        return 1
    print("[doc_sync pre-push] OK: scripts/verify.py passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["pre-commit", "pre-push"])
    args = parser.parse_args()
    root = repo_root()
    if args.mode == "pre-commit":
        return cmd_pre_commit(root)
    return cmd_pre_push(root)


if __name__ == "__main__":
    sys.exit(main())
