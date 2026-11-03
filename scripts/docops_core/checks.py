"""SPEC_STRUCTURE evaluation over one snapshot, plus result assembly.

Per-check status vocabulary (exactly four values, as in the approved
contract; see schemas/docops-result.schema.json):

  PASS   the root SPEC and every registered SPEC that is active meet the
         structural contract
  FAIL   a SPEC (or a registered-but-missing/unmerged/non-regular/directory
         SPEC) violates it, or a file named SPEC.md is not classified, or a
         classification is invalid, conflicting or stale (ADR-0013 Decision 7)
  N/A    nothing to check, proven: no SPEC exists in scope, or every SPEC
         in scope declares itself idle (every SPEC.md is classified, so no
         SPEC lies outside the scope)
  SKIP   defined for the contract and exit code 3; the SPEC scope rules no
         longer produce it

A failure of the checker itself (Git cannot read the requested snapshot,
the policy file is invalid, a blob is unreadable, an internal error) is
not a per-check status. It is a tool failure: the snapshot result carries
an `error` object instead of `checks`, its status is null, and the run
exits 2. It can never be read as N/A or PASS.

DOC_IMPACT_ASSESSMENT is evaluated only when an impact report is supplied
(`--impact-file`, see impact.py and ADR-0014); every other dimension, and
this one without a report, carries `assessed: false` and a reason, and no
`status` at all, so no fifth status value exists. They are separate
fields so they cannot be inferred from, or folded into, the structural
verdict.
"""
from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from . import RESULT_SCHEMA_VERSION, TOOL_NAME, TOOL_VERSION
from . import gitstate, impact as impact_mod, policy as policy_mod, specparse
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
    "not assessed by 'docops check' in this run; SPEC_STRUCTURE is evaluated, and "
    "DOC_IMPACT_ASSESSMENT only when --impact-file is given. No verdict here implies "
    "anything about this dimension"
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


def combined_exit_code(codes: List[int]) -> int:
    """Worst of several non-tool exit codes: 1 (violation, or a required impact assessment
    that is incomplete) over 3 (SPEC applicability not established) over 0."""
    for code in (1, 3):
        if code in codes:
            return code
    return 0


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
                 spec: policy_mod.TrackSpec, optional: bool) -> Optional[dict]:
    found = by_path.get(spec.path)
    if not found and _is_directory(by_path, spec.path):
        return _track_result(
            spec.path, spec.track, FAIL, specparse.STATE_UNKNOWN,
            "SPEC path is a directory in the inspected snapshot, not a file",
            [_diag("SPEC_IS_DIRECTORY", "path %s holds other files but is not itself a SPEC file"
                   % spec.path)],
        )
    if not found:
        if optional:
            return None  # the root SPEC.md is absent and no policy entry names it: no SPEC
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


_CODE_UNCLASSIFIED = "OUT_OF_SCOPE_SPEC"  # code kept from the earlier, warning-only rule


@dataclass(frozen=True)
class Classification:
    """Where every file named SPEC.md of a snapshot stands (ADR-0013 Decision 7)."""
    policy: policy_mod.Policy
    root: policy_mod.TrackSpec        # the root SPEC; evaluated if present or explicitly named
    root_explicit: bool               # the policy has an entry for the root path
    tracks: Tuple[policy_mod.TrackSpec, ...]    # registered active tracks, root excluded
    non_active: Tuple[Tuple[str, str], ...]     # valid listed non-active files: (path, reason)
    unclassified: Tuple[str, ...]     # SPEC.md files in no valid category (listed-but-invalid included)
    findings: Tuple[dict, ...]        # check-level findings; each one makes the check FAIL


def _is_spec_name(path: str) -> bool:
    return posixpath.basename(path).lower() == "spec.md"


def classify_specs(policy: policy_mod.Policy, by_path: Dict[str, List[Entry]]) -> Classification:
    root = None
    tracks = []
    for track in policy.tracks:
        if track.path == policy_mod.ROOT_SPEC_PATH:
            root = track
        else:
            tracks.append(track)
    # The default policy (no policy file) carries the root track so that it is always there,
    # but nothing names it: an absent root is then simply no SPEC, not a missing registration.
    root_explicit = root is not None and not policy.implicit
    if root is None:
        root = policy_mod.TrackSpec(policy_mod.ROOT_SPEC_PATH, policy_mod.DEFAULT_TRACK)
    active_paths = {t.path for t in tracks}

    findings: List[dict] = []
    if not root_explicit:
        # The implicit root is named "root"; a track registered under that name elsewhere would
        # make two tracks of one name, which a reader keying tracks by name would conflate.
        for track in tracks:
            if track.track == root.track and policy_mod.ROOT_SPEC_PATH in by_path:
                findings.append(_diag(
                    "SPEC_CLASSIFICATION_CONFLICT",
                    "track name %r is used by the root SPEC.md and by %s; the name 'root' "
                    "belongs to the root SPEC unless an explicit root entry names it otherwise"
                    % (track.track, track.path)))
    valid: List[Tuple[str, str]] = []
    seen = set()
    duplicated = set()
    listed = set()
    for entry in policy.non_active:
        listed.add(entry.path)
        if entry.path in seen:
            if entry.path not in duplicated:
                duplicated.add(entry.path)
                findings.append(_diag(
                    "NON_ACTIVE_DUPLICATE",
                    "non_active_specs lists %s more than once; list each file once" % entry.path))
            continue
        seen.add(entry.path)
        problems = 0
        if entry.path == policy_mod.ROOT_SPEC_PATH:
            findings.append(_diag(
                "ROOT_LISTED_NON_ACTIVE",
                "the root %s cannot be listed in non_active_specs: the root SPEC is always "
                "active" % entry.path))
            problems += 1
        if entry.path in active_paths:
            findings.append(_diag(
                "SPEC_CLASSIFICATION_CONFLICT",
                "%s is both a registered active track and a non-active file; it must be in "
                "exactly one category" % entry.path))
            problems += 1
        if entry.reason is None or not entry.reason.strip():
            findings.append(_diag(
                "NON_ACTIVE_REASON_MISSING",
                "non_active_specs entry %s has no reason; state why it is not a project "
                "specification" % entry.path))
            problems += 1
        found = by_path.get(entry.path)
        if not found:
            if _is_directory(by_path, entry.path):
                findings.append(_diag(
                    "NON_ACTIVE_NOT_A_FILE",
                    "non_active_specs entry %s is a directory in the snapshot, not a file"
                    % entry.path))
            else:
                findings.append(_diag(
                    "NON_ACTIVE_STALE",
                    "non_active_specs lists %s, which does not exist in the snapshot; remove "
                    "the entry" % entry.path))
            problems += 1
        elif len(found) != 1 or found[0].stage != 0:
            findings.append(_diag(
                "NON_ACTIVE_NOT_A_FILE",
                "non_active_specs entry %s is unmerged in the index (conflict stages present)"
                % entry.path))
            problems += 1
        if not problems:
            valid.append((entry.path, entry.reason.strip()))

    handled = {root.path} | active_paths | {path for path, _ in valid}
    unclassified = tuple(sorted(
        path for path in by_path if _is_spec_name(path) and path not in handled))
    # A listed file whose entry is invalid stays unclassified (it is not in `handled`), but
    # its entry already carries the finding, so only unlisted files get the inventory finding.
    for path in unclassified:
        if path in listed:
            continue
        findings.append(_diag(
            _CODE_UNCLASSIFIED,
            "SPEC file %s is not classified: it is not the root SPEC.md, not a registered active "
            "track and not listed in non_active_specs; register it in specs, list it in "
            "non_active_specs with a reason, or remove it (rule: every file named SPEC.md is "
            "exactly one of these, ADR-0013 Decision 7)" % path))
    return Classification(policy, root, root_explicit, tuple(tracks), tuple(valid),
                          unclassified, tuple(findings))


def spec_candidate_paths(snapshot: Snapshot) -> List[str]:
    """SPEC paths of a snapshot for the impact validator (ADR-0014 Decision 2), taken by the
    same classification as the SPEC check: the root SPEC.md (always, once), the registered
    active tracks, and every SPEC.md that is not validly classified. A validly listed
    non-active file is not a candidate. An unusable policy is never read as an empty one: it
    raises SpecPolicyUnreadable and the caller must not guess."""
    by_path = _entries_by_path(snapshot.entries)
    try:
        policy = _load_policy(snapshot, by_path)
    except policy_mod.PolicyError as exc:
        raise impact_mod.SpecPolicyUnreadable("the SPEC policy %s cannot be used: %s"
                                              % (policy_mod.POLICY_PATH, exc))
    cls = classify_specs(policy, by_path)
    paths = [cls.root.path] + [t.path for t in cls.tracks]
    # Every SPEC-named file that is not the root, a track or a validly listed non-active file.
    accepted = set(paths) | {path for path, _ in cls.non_active}
    paths += sorted(p for p in by_path if _is_spec_name(p) and p not in accepted)
    return paths


def check_spec_structure(snapshot: Snapshot) -> dict:
    """Evaluate the root SPEC and every registered SPEC in the snapshot, and classify every
    file named SPEC.md. SPEC content problems become FAIL; checker/Git/policy problems raise
    ToolFailure."""
    by_path = _entries_by_path(snapshot.entries)
    try:
        policy = _load_policy(snapshot, by_path)
    except policy_mod.PolicyError as exc:
        raise ToolFailure("POLICY_INVALID",
                          "policy %s is invalid: %s" % (policy_mod.POLICY_PATH, exc))
    except GitError as exc:
        raise ToolFailure("POLICY_UNREADABLE", "policy could not be read: %s" % exc)

    cls = classify_specs(policy, by_path)
    tracks = []
    # The root SPEC is evaluated exactly once, first. It may be absent unless the policy
    # names it, in which case its absence is a FAIL like any registered path.
    for spec, optional in [(cls.root, not cls.root_explicit)] + [(t, False) for t in cls.tracks]:
        result = _check_track(snapshot, by_path, spec, optional)
        if result is not None:
            tracks.append(result)

    findings = list(cls.findings)

    if findings and FAIL in [t["status"] for t in tracks]:
        status = FAIL
        reason = ("one or more SPEC tracks did not pass and %d SPEC classification problem(s) "
                  "were found (see tracks and diagnostics)" % len(findings))
    elif findings:
        status = FAIL
        reason = ("%d SPEC classification problem(s): a file named SPEC.md is not classified, or "
                  "a classification is invalid, conflicting or stale (see diagnostics)"
                  % len(findings))
    elif tracks:
        status = aggregate([t["status"] for t in tracks])
        if status == NA:
            reason = "every SPEC in scope explicitly declares no active task"
        elif status == PASS:
            reason = "every active SPEC in scope meets the structural contract"
        else:
            reason = "one or more SPEC tracks did not pass (see tracks)"
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
        "non_active_specs": [{"path": path, "reason": why} for path, why in cls.non_active],
        "diagnostics": findings,
    }


def _dimensions(spec_structure: dict, impact: Optional[dict] = None) -> dict:
    checks = {"SPEC_STRUCTURE": spec_structure}
    for name in DIMENSIONS[1:]:
        checks[name] = {"assessed": False, "reason": _NOT_ASSESSED_REASON}
    if impact is not None:
        checks["DOC_IMPACT_ASSESSMENT"] = impact
    return checks


def snapshot_result(request: dict, snapshot_info: dict, spec_structure: dict,
                    impact: Optional[dict] = None) -> dict:
    statuses = [spec_structure["status"]]
    codes = [exit_code_for(spec_structure["status"])]
    if impact is not None and impact.get("assessed"):
        statuses.append(impact["status"])
        # A required assessment that is not satisfied (FAIL) or not complete (SKIP: missing,
        # stale or unverifiable evidence) never exits 0, and uses the violation code; the
        # per-check status stays SKIP. Exit 3 stays what Stage 1 made it: SPEC applicability.
        codes.append(0 if impact["status"] == PASS else 1)
    status = aggregate(statuses)
    return {
        "request": request,
        "snapshot": snapshot_info,
        "checks": _dimensions(spec_structure, impact),
        "error": None,
        "status": status,
        "exit_code": combined_exit_code(codes),
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


def deleted_snapshot_result(request: dict,
                            report: Optional[impact_mod.ParsedReport] = None) -> dict:
    spec_structure = {
        "status": NA,
        "reason": "ref deletion: no tree is published, so there is no SPEC to check",
        "policy": {"source": "none", "path": None},
        "tracks": [],
        "non_active_specs": [],
        "diagnostics": [],
    }
    info = {"kind": "deleted-ref", "commit": None, "tree": None, "index_digest": None}
    impact = None
    if report is not None:
        if report.data is None:
            # Structure does not depend on a subject: a malformed report is a FAIL here too.
            impact = impact_mod.assess(report, None, None)
        else:
            impact = impact_mod.not_assessed(
                "ref deletion: no tree is published, so the supplied impact report has no "
                "subject")
    return snapshot_result(request, info, spec_structure, impact)


def check_request(repo: gitstate.Repository, request: dict,
                  report: Optional[impact_mod.ParsedReport] = None) -> dict:
    """Resolve and check one request. Any failure becomes a tool-error result;
    this function does not raise. `report` is the parsed impact report, if one
    was supplied; it is validated against exactly this snapshot."""
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
        impact = None
        if report is not None:
            impact = impact_mod.assess(report, snapshot, request.get("remote_oid"),
                                       spec_candidate_paths)
    except ToolFailure as exc:
        return error_snapshot_result(request, exc.code, exc.message, info)
    except GitError as exc:
        return error_snapshot_result(request, "IMPACT_GIT_ERROR",
                                     "Git failed while validating the impact report: %s" % exc, info)
    except Exception as exc:  # noqa: BLE001
        return error_snapshot_result(request, "INTERNAL_ERROR",
                                     "%s: %s" % (type(exc).__name__, exc), info)
    return snapshot_result(request, info, spec_structure, impact)


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
        summary = {"status": status,
                   "exit_code": combined_exit_code([s["exit_code"] for s in snapshots]),
                   "tool_errors": 0}
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
