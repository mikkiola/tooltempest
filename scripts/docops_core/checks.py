"""SPEC_STRUCTURE evaluation over one snapshot, plus result assembly.

Per-check status vocabulary (exactly four values, as in the approved
contract; see schemas/docops-result.schema.json):

  PASS   every registered SPEC that is active meets the structural contract
  FAIL   a SPEC (or a registered-but-missing/unmerged/non-regular/directory
         SPEC) violates it
  N/A    nothing to check, proven: no SPEC exists in scope, or every SPEC
         in scope declares itself idle and no SPEC lies outside the scope
  SKIP   applicability cannot be established (SPEC files exist outside the
         registered scope that were not evaluated)

A failure of the checker itself (Git cannot read the requested snapshot,
the policy file is invalid, a blob is unreadable, an internal error) is
not a per-check status. It is a tool failure: the snapshot result carries
an `error` object instead of `checks`, its status is null, and the run
exits 2. It can never be read as N/A or PASS.

The other result dimensions carry `assessed: false` and a reason, and no
`status` at all, so no fifth status value exists. They are separate
fields so they cannot be inferred from, or folded into, the structural
verdict.
"""
from __future__ import annotations

import posixpath
from typing import Dict, List, Optional

from . import RESULT_SCHEMA_VERSION, TOOL_NAME, TOOL_VERSION
from . import gitstate, policy as policy_mod, specparse
from .gitstate import Entry, GitError, Snapshot

PASS = "PASS"
FAIL = "FAIL"
NA = "N/A"
SKIP = "SKIP"

# Worst first: the aggregate of several statuses is the earliest one present.
_PRECEDENCE = (FAIL, SKIP, PASS, NA)
_EXIT_CODES = {PASS: 0, NA: 0, FAIL: 1, SKIP: 3}
EXIT_TOOL_ERROR = 2

DIMENSIONS = (
    "SPEC_STRUCTURE",
    "TESTS",
    "DOC_IMPACT_ASSESSMENT",
    "DOC_CONSISTENCY_REVIEW",
    "ACCEPTANCE_VERIFICATION",
    "PUBLICATION",
    "OWNER_ACCEPTANCE",
)
_NOT_ASSESSED_REASON = (
    "not assessed by 'docops check' in this version; only SPEC_STRUCTURE is evaluated, "
    "and no verdict here implies anything about this dimension"
)


class ToolFailure(Exception):
    """The checker could not produce a verdict for a snapshot (not a SPEC finding)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def aggregate(statuses: List[str]) -> str:
    for status in _PRECEDENCE:
        if status in statuses:
            return status
    return NA


def exit_code_for(status: str) -> int:
    return _EXIT_CODES[status]


def _diag(code: str, message: str, line: Optional[int] = None) -> dict:
    return {"code": code, "message": message, "line": line}


def _track_result(path: str, track: str, status: str, state: str, reason: str,
                  diagnostics=None, milestones=None) -> dict:
    return {
        "track": track,
        "path": path,
        "state": state,
        "status": status,
        "reason": reason,
        "milestones": milestones,
        "diagnostics": diagnostics or [],
    }


def _entries_by_path(entries: List[Entry]) -> Dict[str, List[Entry]]:
    grouped: Dict[str, List[Entry]] = {}
    for entry in entries:
        grouped.setdefault(entry.path, []).append(entry)
    return grouped


def _is_directory(by_path: Dict[str, List[Entry]], path: str) -> bool:
    """True if the snapshot holds entries beneath `path`, i.e. it is a directory."""
    prefix = path + "/"
    return any(other.startswith(prefix) for other in by_path)


def _load_policy(snapshot: Snapshot, by_path: Dict[str, List[Entry]]) -> policy_mod.Policy:
    found = by_path.get(policy_mod.POLICY_PATH)
    if not found:
        if _is_directory(by_path, policy_mod.POLICY_PATH):
            raise policy_mod.PolicyError("%s is a directory, not a file" % policy_mod.POLICY_PATH)
        return policy_mod.default_policy()
    if len(found) != 1 or found[0].stage != 0:
        raise policy_mod.PolicyError("%s is unmerged in this snapshot" % policy_mod.POLICY_PATH)
    if found[0].mode not in gitstate.MODE_REGULAR:
        raise policy_mod.PolicyError("%s is not a regular file" % policy_mod.POLICY_PATH)
    return policy_mod.parse_policy(snapshot.read_blob(found[0].oid))


def _check_track(snapshot: Snapshot, by_path: Dict[str, List[Entry]],
                 spec: policy_mod.TrackSpec, implicit: bool) -> Optional[dict]:
    found = by_path.get(spec.path)
    if not found and _is_directory(by_path, spec.path):
        return _track_result(
            spec.path, spec.track, FAIL, specparse.STATE_UNKNOWN,
            "SPEC path is a directory in the inspected snapshot, not a file",
            [_diag("SPEC_IS_DIRECTORY", "path %s holds other files but is not itself a SPEC file"
                   % spec.path)],
        )
    if not found:
        if implicit:
            return None  # no root SPEC and no registry: the normal "no SPEC" state
        return _track_result(
            spec.path, spec.track, FAIL, specparse.STATE_UNKNOWN,
            "registered SPEC is not present in the inspected snapshot",
            [_diag("SPEC_MISSING", "path %s is registered but absent" % spec.path)],
        )
    if len(found) != 1 or found[0].stage != 0:
        return _track_result(
            spec.path, spec.track, FAIL, specparse.STATE_UNKNOWN,
            "SPEC is unmerged in the index (conflict stages present)",
            [_diag("SPEC_UNMERGED", "index holds conflict stages %s for this path"
                   % sorted(e.stage for e in found))],
        )
    entry = found[0]
    if entry.mode not in gitstate.MODE_REGULAR:
        return _track_result(
            spec.path, spec.track, FAIL, specparse.STATE_UNKNOWN,
            "SPEC is not a regular file in the snapshot (mode %s)" % entry.mode,
            [_diag("SPEC_NOT_REGULAR", "mode %s: symlinks and submodules are never followed"
                   % entry.mode)],
        )
    try:
        data = snapshot.read_blob(entry.oid)
    except GitError as exc:
        raise ToolFailure("BLOB_UNREADABLE",
                          "SPEC blob for %s could not be read: %s" % (spec.path, exc))
    verdict = specparse.analyze_spec(data)
    return _track_result(
        spec.path, spec.track, verdict.status, verdict.state, verdict.reason,
        [d.as_dict() for d in verdict.diagnostics], verdict.milestones,
    )


def check_spec_structure(snapshot: Snapshot) -> dict:
    """Evaluate every registered SPEC in the snapshot. SPEC content problems
    become FAIL; checker/Git/policy problems raise ToolFailure."""
    by_path = _entries_by_path(snapshot.entries)
    try:
        policy = _load_policy(snapshot, by_path)
    except policy_mod.PolicyError as exc:
        raise ToolFailure("POLICY_INVALID",
                          "policy %s is invalid: %s" % (policy_mod.POLICY_PATH, exc))
    except GitError as exc:
        raise ToolFailure("POLICY_UNREADABLE", "policy could not be read: %s" % exc)

    registered = {t.path for t in policy.tracks}
    tracks = []
    for spec in policy.tracks:
        result = _check_track(snapshot, by_path, spec, policy.implicit)
        if result is not None:
            tracks.append(result)

    out_of_scope = sorted(
        path for path in by_path
        if posixpath.basename(path).lower() == "spec.md" and path not in registered
    )
    warnings = [
        _diag(
            "OUT_OF_SCOPE_SPEC",
            "SPEC file %s is outside the registered scope and was not evaluated; register "
            "it in %s to check it" % (path, policy_mod.POLICY_PATH),
        )
        for path in out_of_scope
    ]

    if tracks:
        status = aggregate([t["status"] for t in tracks])
        if status == NA and out_of_scope:
            # "No active task" is proven only for the registered tracks; an
            # unevaluated SPEC elsewhere may say otherwise, so N/A is not established.
            status = SKIP
            reason = (
                "every SPEC in scope declares no active task, but %d SPEC file(s) outside the "
                "registered scope were not evaluated; applicability cannot be established"
                % len(out_of_scope)
            )
        elif status == NA:
            reason = "every SPEC in scope explicitly declares no active task"
        elif status == PASS:
            reason = "every active SPEC in scope meets the structural contract"
        else:
            reason = "one or more SPEC tracks did not pass (see tracks)"
    elif out_of_scope:
        status = SKIP
        reason = (
            "no SPEC is registered in scope, but %d SPEC file(s) exist outside it; "
            "applicability cannot be established" % len(out_of_scope)
        )
    else:
        status = NA
        reason = (
            "no registered SPEC and no SPEC.md in this snapshot (normal state: no active SPEC)"
            if policy.source == "snapshot"
            else "no SPEC exists in this snapshot (normal state: no active SPEC)"
        )

    return {
        "status": status,
        "reason": reason,
        "policy": {"source": policy.source,
                   "path": policy_mod.POLICY_PATH if policy.source == "snapshot" else None},
        "tracks": tracks,
        "diagnostics": warnings,
    }


def _dimensions(spec_structure: dict) -> dict:
    checks = {"SPEC_STRUCTURE": spec_structure}
    for name in DIMENSIONS[1:]:
        checks[name] = {"assessed": False, "reason": _NOT_ASSESSED_REASON}
    return checks


def snapshot_result(request: dict, snapshot_info: dict, spec_structure: dict) -> dict:
    status = spec_structure["status"]
    return {
        "request": request,
        "snapshot": snapshot_info,
        "checks": _dimensions(spec_structure),
        "error": None,
        "status": status,
        "exit_code": exit_code_for(status),
    }


def error_snapshot_result(request: dict, code: str, message: str,
                          snapshot_info: Optional[dict] = None) -> dict:
    """A snapshot the checker could not evaluate: no checks, no status."""
    info = snapshot_info or {"kind": "unresolved", "commit": None, "tree": None,
                             "index_digest": None}
    return {
        "request": request,
        "snapshot": info,
        "checks": None,
        "error": {"code": code, "message": message},
        "status": None,
        "exit_code": EXIT_TOOL_ERROR,
    }


def deleted_snapshot_result(request: dict) -> dict:
    spec_structure = {
        "status": NA,
        "reason": "ref deletion: no tree is published, so there is no SPEC to check",
        "policy": {"source": "none", "path": None},
        "tracks": [],
        "diagnostics": [],
    }
    info = {"kind": "deleted-ref", "commit": None, "tree": None, "index_digest": None}
    return snapshot_result(request, info, spec_structure)


def check_request(repo: gitstate.Repository, request: dict) -> dict:
    """Resolve and check one request. Any failure becomes a tool-error result;
    this function does not raise."""
    try:
        if request["kind"] == "index":
            snapshot = gitstate.index_snapshot(repo)
        else:
            snapshot = gitstate.commit_snapshot(repo, request["rev"])
    except GitError as exc:
        return error_snapshot_result(request, "SNAPSHOT_UNREADABLE",
                                     "snapshot could not be read: %s" % exc)
    except Exception as exc:  # noqa: BLE001 - fail closed on any checker fault
        return error_snapshot_result(request, "INTERNAL_ERROR",
                                     "%s: %s" % (type(exc).__name__, exc))

    info = {"kind": snapshot.kind, **snapshot.identity}
    try:
        spec_structure = check_spec_structure(snapshot)
    except ToolFailure as exc:
        return error_snapshot_result(request, exc.code, exc.message, info)
    except Exception as exc:  # noqa: BLE001
        return error_snapshot_result(request, "INTERNAL_ERROR",
                                     "%s: %s" % (type(exc).__name__, exc), info)
    return snapshot_result(request, info, spec_structure)


def build_result(command: str, repository_root: Optional[str], snapshots: List[dict],
                 error: Optional[str] = None) -> dict:
    """Assemble the top-level document. `error` marks a failure before any
    snapshot could be evaluated. Any tool error anywhere makes the summary
    status null and the exit code 2: partial results are never summarised
    as a verdict."""
    if error is None and not snapshots:
        error = "no snapshot was requested"
    tool_errors = sum(1 for s in snapshots if s["error"] is not None) + (1 if error else 0)
    if tool_errors:
        summary = {"status": None, "exit_code": EXIT_TOOL_ERROR, "tool_errors": tool_errors}
    else:
        status = aggregate([s["status"] for s in snapshots])
        summary = {"status": status, "exit_code": exit_code_for(status), "tool_errors": 0}
    if error is not None:
        summary["reason"] = error
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "command": command,
        "repository": {"root": repository_root},
        "snapshots": snapshots,
        "summary": summary,
    }
