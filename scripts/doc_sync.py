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

- RECONCILE never writes anything: a CHECKPOINT.md block missing
  `verify:`/`done-when:`/`status:` blocks the whole commit instead,
  staged or not, reporting a DRIFT/QUESTION pair per missing field. It
  never invents a field's *value* -- not even an honest `TODO`
  placeholder, since writing anything into the file without a human
  decision authorizing that value is itself the violation (Hub Rules
  v3.6 Rule 3). It never auto-fixes an inline `## Milestones` checkbox
  with an empty description either, for the same reason.
- Because RECONCILE never writes, there is nothing for a VALIDATE
  failure to restore -- if `scripts/verify.py` still fails at VALIDATE,
  it is for a reason RECONCILE was never going to touch (e.g. an empty
  `## Milestones` checkbox description).
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


def run_verify(root: Path) -> tuple[int, list | None, str, bool]:
    """Runs scripts/verify.py and returns (exit_code, parsed_json_or_None,
    stderr_text, crashed). Treated as the single source of structural truth
    for both DETECT and VALIDATE, per ADR-0001. A non-zero exit code alone
    does not mean failure here -- scripts/verify.py exits 1 whenever any
    discovered file is MALFORMED, which is the expected, normal state
    for DETECT to observe before RECONCILE has run. Callers decide what
    a given exit code means for their own step.

    `crashed` is True whenever stdout failed to parse as JSON at all --
    i.e. scripts/verify.py itself never produced its normal structured
    output (missing file, syntax error, unhandled exception -- or the
    legitimate "no SPEC.md files found" exit-2 no-op, which also prints
    no JSON). DETECT already special-cases exit code 2 separately and
    can ignore this signal; VALIDATE and pre-push, which don't, can use
    it to tell "verify.py itself is the problem" apart from "verify.py
    ran fine and reported a real structural finding."
    """
    verify_path = root / "scripts" / "verify.py"
    if not verify_path.is_file():
        return 1, None, f"scripts/verify.py not found at {verify_path}", True
    proc = subprocess.run(
        [sys.executable, str(verify_path)],
        cwd=root, capture_output=True, text=True,
    )
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError:
        parsed = None
    return proc.returncode, parsed, proc.stderr, parsed is None


def staged_files(root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
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


def reconcile(root: Path, verify_results: list, staged: set[str]) -> dict:
    """Structural-only check over CHECKPOINT.md files. Never writes
    anything: a `## <heading>` block missing `verify:`/`done-when:`/
    `status:` blocks the whole commit instead, staged or not (Hub Rules
    v3.6 Rule 3 -- ask one specific question, don't fill the gap
    yourself; even an honest `TODO` placeholder still fills it,
    structurally). Never touches inline `## Milestones` findings either
    (no safe auto-fix exists for those).

    Missing fields are read from the *staged* blob when the file is
    staged -- that is what will actually be committed -- falling back
    to the working-tree copy `scripts/verify.py` already scanned
    otherwise (an untouched, not-yet-staged file this commit doesn't
    even involve).

    `staged` is the caller's already-collected `staged_files(root)` result
    (the caller needs it before this check runs too, for the
    UNKNOWN-pattern touched-component check -- passed in here rather
    than re-shelling out to git a second time for the same data).

    Returns:
      {
        "blocked": [{"path": relative_path, "missing": [field, ...]}, ...],
        "scanned": int, "affected": int,
      }
    When `blocked` is non-empty, no file has been touched -- the caller
    must stop before VALIDATE.
    """
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

    blocked: list[dict] = []
    for source_file in checkpoint_candidates:
        rel = relative_to_root(root, source_file)
        text = staged_blob_text(root, rel) if rel in staged else None
        if text is None:
            text = (root / rel).read_text(encoding="utf-8")
        missing = sorted(
            {name for finding in find_checkpoint_missing_fields(text) for name in finding["missing"]},
            key=REQUIRED_FIELD_ORDER.index,
        )
        if missing:
            blocked.append({"path": rel, "missing": missing})

    return {"blocked": blocked, "scanned": scanned, "affected": affected}


def make_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"docops_{ts}_{secrets.token_hex(3)}"


# Arbitrary starting point, not a tuned value -- revisit if run volume in
# practice suggests a different number is warranted.
RUN_RECORD_RETENTION = 50


def prune_run_records(runs_dir: Path) -> list[Path]:
    """Keeps only the RUN_RECORD_RETENTION most recent run records. The
    run_id's ISO8601 timestamp prefix (docops_<ISO8601>_<suffix>.json)
    sorts chronologically as a plain filename string, so the oldest
    records are simply the first N in sorted order. Returns the paths
    deleted -- these records are git-tracked (see ADR-0001, One Commit
    SHA Lineage), so the caller must stage their removal too, not just
    delete them from disk."""
    records = sorted(runs_dir.glob("docops_*.json"))
    excess = len(records) - RUN_RECORD_RETENTION
    if excess <= 0:
        return []
    pruned = records[:excess]
    for old in pruned:
        old.unlink()
    return pruned


def write_record(
    root: Path, run_id: str, hook: str, started_at: str, finished_at: str,
    total_duration_sec: float, steps: list[str], step_durations_ms: dict,
    counters: dict, timeline_summary: str,
) -> tuple[Path, list[Path]]:
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
    pruned = prune_run_records(runs_dir)
    return out_path, pruned


def cmd_pre_commit(root: Path) -> int:
    start_wall = datetime.now(timezone.utc)
    start_perf = time.monotonic()
    steps: list[str] = []
    step_durations_ms: dict[str, int] = {}

    t0 = time.monotonic()
    steps.append("detect")
    exit_code, verify_results, verify_stderr, _verify_crashed = run_verify(root)
    step_durations_ms["detect"] = int((time.monotonic() - t0) * 1000)

    if verify_results is None:
        if exit_code == 2:
            print("[doc_sync pre-commit] OK: no SPEC.md files found in this project. Nothing to do.")
            return 0
        # _verify_crashed is always True here already (verify_results is
        # None implies it) -- captured only for symmetry with VALIDATE and
        # pre-push below, which don't already have this distinction.
        print(
            "[doc_sync pre-commit] FAIL: scripts/verify.py did not produce readable "
            "output (structural discovery could not run). This usually means "
            "scripts/verify.py itself is missing, has a syntax error, or crashed "
            "-- see the error below. If the error doesn't look related to anything "
            "you changed, this is likely a bug in scripts/verify.py itself, not "
            "your commit -- report it rather than trying to work around it.",
            file=sys.stderr,
        )
        if verify_stderr:
            print(verify_stderr, file=sys.stderr)
        return 1

    staged = staged_files(root)

    # UNKNOWN pattern (no CHECKPOINT.md, no '## Milestones' checklist --
    # source_file: null) is dropped by the doc_owned filter below before
    # it ever reaches a MALFORMED check, so it needs its own gate here.
    # Only blocks when the component is actually touched by this commit
    # (its SPEC.md is staged, or a staged path falls under its directory)
    # -- an untouched, still-being-scaffolded component must not block an
    # unrelated commit.
    unknown_touched: list[str] = []
    for entry in verify_results:
        if entry.get("pattern") != "UNKNOWN":
            continue
        spec_path = entry.get("spec_path")
        if not spec_path:
            continue
        rel_spec = relative_to_root(root, spec_path)
        spec_dir_parts = Path(rel_spec).parent.parts
        touched = rel_spec in staged or (
            bool(spec_dir_parts)
            and any(Path(s).parts[: len(spec_dir_parts)] == spec_dir_parts for s in staged)
        )
        if touched:
            unknown_touched.append(rel_spec)

    if unknown_touched:
        print(
            "[doc_sync pre-commit] FAIL: staged component(s) have an unrecognized "
            "doc structure (UNKNOWN pattern) and cannot be verified:",
            file=sys.stderr,
        )
        for rel in unknown_touched:
            print(f"  - {rel}", file=sys.stderr)
        print(
            "[doc_sync pre-commit] Add a CHECKPOINT.md, or a '## Milestones' "
            "checklist with checkbox lines, for this component before "
            "committing changes to it.",
            file=sys.stderr,
        )
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
    result = reconcile(root, verify_results, staged)
    step_durations_ms["reconcile"] = int((time.monotonic() - t0) * 1000)

    if result["blocked"]:
        print(
            "[doc_sync pre-commit] FAIL: staged doc-owned file(s) are missing "
            "required field(s):",
            file=sys.stderr,
        )
        for item in result["blocked"]:
            for field in item["missing"]:
                print(f"[DRIFT] {item['path']}: missing required field `{field}`", file=sys.stderr)
                print(f"[QUESTION] What should `{field}` be?", file=sys.stderr)
        print(
            "[doc_sync pre-commit] DocOps never invents a field's value -- not "
            "even an honest TODO placeholder. Answer the question(s) above by "
            "hand in the file itself, then re-stage and retry.",
            file=sys.stderr,
        )
        return 1

    t0 = time.monotonic()
    steps.append("validate")
    validate_exit, _validate_results, validate_stderr, validate_crashed = run_verify(root)
    step_durations_ms["validate"] = int((time.monotonic() - t0) * 1000)

    if validate_exit != 0:
        if validate_crashed:
            print(
                "[doc_sync pre-commit] FAIL: scripts/verify.py did not produce "
                "readable output (structural discovery could not run). This "
                "usually means scripts/verify.py itself is missing, has a "
                "syntax error, or crashed -- see the error below. Nothing was "
                "touched, so there is nothing to restore. If the error doesn't "
                "look related to anything you changed, this is likely a bug in "
                "scripts/verify.py itself, not your commit -- report it rather "
                "than trying to work around it.",
                file=sys.stderr,
            )
        else:
            print(
                "[doc_sync pre-commit] FAIL: scripts/verify.py still failed even "
                "though no CHECKPOINT.md field was missing -- likely an inline "
                "'## Milestones' checkbox with an empty description, which is "
                "never auto-fixed. Fix the issue reported below in your working "
                "tree, then re-stage and retry.",
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
        "scanned": result["scanned"], "affected": result["affected"], "updated": 0,
    }
    timeline_summary = (
        f"Scanned {result['scanned']} doc-owned file(s); {result['affected']} structurally "
        "malformed; validate passed."
    )
    run_id = make_run_id()
    record_path, pruned_paths = write_record(
        root, run_id, "pre-commit", start_wall.isoformat(), finished_wall.isoformat(),
        total_duration_sec, steps, step_durations_ms, counters, timeline_summary,
    )
    step_durations_ms["record"] = int((time.monotonic() - t0) * 1000)

    git_add_paths = (
        [str(record_path.relative_to(root))]
        + [str(p.relative_to(root)) for p in pruned_paths]
    )
    subprocess.run(["git", "add", *git_add_paths], cwd=root, check=True)

    print(f"[doc_sync pre-commit] OK: staged {record_path.relative_to(root)}.")
    return 0


def cmd_pre_push(root: Path) -> int:
    """Hard validation only: runs scripts/verify.py and requires exit
    code 0. Never modifies the working tree, never stages anything,
    never writes a git note, never makes a network call."""
    exit_code, _results, stderr_text, crashed = run_verify(root)
    if exit_code != 0:
        if crashed:
            print(
                "[doc_sync pre-push] FAIL: scripts/verify.py did not produce "
                "readable output (structural discovery could not run). This "
                "usually means scripts/verify.py itself is missing, has a "
                "syntax error, or crashed -- see the error below. If the error "
                "doesn't look related to anything you changed, this is likely "
                "a bug in scripts/verify.py itself, not your commit -- report "
                "it rather than trying to work around it.",
                file=sys.stderr,
            )
        else:
            print(
                "[doc_sync pre-push] FAIL: scripts/verify.py did not pass -- see "
                "the detail below for which file(s)/component(s) are "
                "structurally invalid. Fix them, commit, and push again; this "
                "push stays blocked until scripts/verify.py exits 0.",
                file=sys.stderr,
            )
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
