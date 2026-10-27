"""DOC_IMPACT_ASSESSMENT: deterministic validation of an impact report.

The report is written by whoever reviewed the change (a person or an
agent) and supplied with `--impact-file`. This module never writes,
generates or interprets one. It establishes four things and nothing
else (ADR-0014):

  structure     the report has the fields its mode and profile require
  identity      its subject tree is the snapshot under check, and its base
                tree is a real tree (and, when `docops` is given the old tip
                of a push, that tip's tree)
  completeness  every change between the reported base and the subject is
                named by the report, and every category (or the
                minimal-change reason) cites evidence
  freshness     each piece of evidence was gathered on a tree and over a
                scope; it is stale if a path in its scope differs between
                that tree and the subject

Whether the report is *true* (that a change is small, that a category is
unaffected, that a document is consistent) is not decided here. A PASS
means the review is recorded, bound to this snapshot and current; it is
never evidence that the documentation is correct. Statuses are the four
of the approved contract: PASS, FAIL, SKIP (incomplete, stale or not
verifiable: renewed review required), and N/A is never produced.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import gitstate
from .gitstate import GitError, Snapshot

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

REPORT_SCHEMA_VERSION = 1
MAX_REPORT_BYTES = 1 << 20
MAX_DIAGNOSTICS = 100

# The five approved entry modes (Stage 0 plan, section B). They are entries
# to one process, not five procedures.
MODES = ("spec_driven", "plan_driven", "audit_test_driven", "direct_fix", "follow_up")
_NO_PLAN_NO_SPEC_MODES = ("plan_driven", "audit_test_driven", "direct_fix", "follow_up")
_NO_PLAN_MODES = ("audit_test_driven", "direct_fix", "follow_up")

PROFILES = ("minimal", "full")
CATEGORIES = (
    "architecture",
    "interfaces",
    "failure_behaviour",
    "configuration",
    "tests",
    "decisions",
    "acceptance",
    "planning_and_operations",
)
EVIDENCE_KINDS = ("test", "review", "inspection")
OUTCOMES = ("pass", "fail", "partial", "skipped")
VALIDATED = ["structure", "identity", "completeness", "freshness"]

_OID_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f\\]")

_TOP_KEYS = ("schema_version", "mode", "process", "plan", "base_tree", "subject_tree",
             "profile", "minimal", "categories", "evidence")
_PROCESS_KEYS = ("spec_skill_invoked", "spec_created", "plan_created")
_EVIDENCE_KEYS = ("id", "kind", "tree", "scope_paths", "outcome", "summary")
_TEST_KEYS = ("run", "passed", "failed", "skipped")


@dataclass
class ParsedReport:
    source: str
    path: Optional[str]
    sha256: str
    size: int
    data: Optional[dict]  # the report when it is structurally valid, else None
    diagnostics: List[dict]


class SpecPolicyUnreadable(Exception):
    """The SPEC policy of a snapshot cannot be read or parsed (raised by the track lookup)."""


class _Diags:
    def __init__(self) -> None:
        self.items: List[dict] = []
        self._dropped: List[str] = []  # effects of diagnostics beyond the cap

    def add(self, code: str, effect: str, message: str, where: Optional[str] = None) -> None:
        if len(self.items) < MAX_DIAGNOSTICS:
            self.items.append({"code": code, "effect": effect, "message": message, "where": where})
        else:
            self._dropped.append(effect)

    def has(self, effect: str) -> bool:
        return any(d["effect"] == effect for d in self.items) or effect in self._dropped

    def finish(self) -> List[dict]:
        if self._dropped:
            # The overflow note carries the most severe effect it hides, so a cap can
            # neither soften a FAIL nor harden a SKIP.
            worst = "fail" if "fail" in self._dropped else "skip" if "skip" in self._dropped else "note"
            self.items.append({
                "code": "TOO_MANY_DIAGNOSTICS", "effect": worst, "where": None,
                "message": "%d further diagnostics were not listed" % len(self._dropped)})
            self._dropped = []
        return self.items


# --- loading and structure ---------------------------------------------------

def _no_duplicates(pairs):
    seen = set()
    for key, _ in pairs:
        if key in seen:
            raise ValueError("duplicate object key %r" % key)
        seen.add(key)
    return dict(pairs)


def load_report(raw: bytes, source: str, path: Optional[str]) -> ParsedReport:
    """Parse and structurally validate one report. Never raises on report content."""
    diags = _Diags()
    digest = hashlib.sha256(raw).hexdigest()
    data = None
    if len(raw) > MAX_REPORT_BYTES:
        diags.add("REPORT_TOO_LARGE", "fail",
                  "report is larger than %d bytes; it was not parsed" % MAX_REPORT_BYTES)
    else:
        try:
            text = raw.decode("utf-8")
            parsed = json.loads(text, object_pairs_hook=_no_duplicates)
        except UnicodeDecodeError as exc:
            diags.add("REPORT_NOT_UTF8", "fail", "report is not valid UTF-8: %s" % exc.reason)
        except (ValueError, RecursionError) as exc:
            diags.add("REPORT_NOT_JSON", "fail", "report is not valid JSON: %s" % exc)
        else:
            if _validate(parsed, diags):
                data = parsed
    return ParsedReport(source, path, digest, len(raw), data, diags.finish())


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _nonblank(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def valid_path(value, allow_dot: bool = False) -> bool:
    """A repository-relative POSIX path (one trailing slash allowed), or "." where permitted."""
    if not isinstance(value, str) or not value:
        return False
    if value == ".":
        return allow_dot
    if _CONTROL_RE.search(value) or value.startswith("/"):
        return False
    parts = value[:-1].split("/") if value.endswith("/") else value.split("/")
    return all(part not in ("", ".", "..") for part in parts)


def _norm(path: str) -> str:
    return path[:-1] if path.endswith("/") and path != "/" else path


def covers(scope: str, path: str) -> bool:
    scope = _norm(scope)
    return scope == "." or path == scope or path.startswith(scope + "/")


def _check_keys(obj: dict, allowed: Tuple[str, ...], where: str, diags: _Diags) -> None:
    for key in obj:
        if key not in allowed:
            diags.add("REPORT_UNKNOWN_FIELD", "fail",
                      "field %r is not part of the report format" % key, "%s.%s" % (where, key))


def _require(obj: dict, key: str, where: str, diags: _Diags) -> bool:
    if key not in obj:
        diags.add("REPORT_FIELD_MISSING", "fail", "required field %r is missing" % key, where)
        return False
    return True


def _paths(value, where: str, diags: _Diags, allow_dot: bool, minimum: int = 1) -> List[str]:
    if not isinstance(value, list):
        diags.add("REPORT_FIELD_TYPE", "fail", "must be a list of paths", where)
        return []
    if len(value) < minimum:
        diags.add("REPORT_FIELD_EMPTY", "fail", "must list at least %d path(s)" % minimum, where)
    good = []
    for index, item in enumerate(value):
        if valid_path(item, allow_dot):
            good.append(_norm(item))
        else:
            diags.add("REPORT_PATH_INVALID", "fail",
                      "%r is not a repository-relative path (no leading slash, '..', empty or "
                      "control characters)" % (item if isinstance(item, str) else type(item).__name__),
                      "%s[%d]" % (where, index))
    return good


def _evidence_ids(value, where: str, diags: _Diags) -> List[str]:
    if not isinstance(value, list) or not value:
        diags.add("REPORT_FIELD_TYPE", "fail", "must be a non-empty list of evidence ids", where)
        return []
    good = []
    for index, item in enumerate(value):
        if isinstance(item, str) and _ID_RE.fullmatch(item):
            good.append(item)
        else:
            diags.add("REPORT_FIELD_TYPE", "fail", "evidence id must match [A-Za-z0-9._-]{1,64}",
                      "%s[%d]" % (where, index))
    return good


def _oid_field(obj: dict, key: str, diags: _Diags) -> None:
    if _require(obj, key, key, diags):
        value = obj[key]
        if not (isinstance(value, str) and _OID_RE.fullmatch(value)):
            diags.add("REPORT_IDENTITY_INVALID", "fail",
                      "%s must be a full lowercase hexadecimal tree OID (40 or 64 digits)" % key, key)


def _validate(data, diags: _Diags) -> bool:
    if not isinstance(data, dict):
        diags.add("REPORT_FIELD_TYPE", "fail", "the report must be a JSON object")
        return False
    _check_keys(data, _TOP_KEYS, "$", diags)
    if _require(data, "schema_version", "schema_version", diags):
        if not (_is_int(data["schema_version"]) and data["schema_version"] == REPORT_SCHEMA_VERSION):
            diags.add("REPORT_SCHEMA_VERSION", "fail",
                      "schema_version must be %d" % REPORT_SCHEMA_VERSION, "schema_version")
    mode = data.get("mode")
    if _require(data, "mode", "mode", diags) and mode not in MODES:
        diags.add("REPORT_MODE_INVALID", "fail",
                  "mode must be one of %s" % ", ".join(MODES), "mode")
        mode = None
    _oid_field(data, "base_tree", diags)
    _oid_field(data, "subject_tree", diags)
    if (isinstance(data.get("base_tree"), str) and data.get("base_tree") == data.get("subject_tree")
            and _OID_RE.fullmatch(data["base_tree"])):
        # The format has no way to say "nothing changed" (a report names at least one
        # affected path), so an empty comparison cannot describe the change it claims to.
        diags.add("REPORT_BASE_EQUALS_SUBJECT", "fail",
                  "base_tree equals subject_tree: there is no change between them for the report "
                  "to describe, and the format has no representation of a no-op", "base_tree")
    _validate_mode(data, mode, diags)
    profile = data.get("profile")
    if _require(data, "profile", "profile", diags) and profile not in PROFILES:
        diags.add("REPORT_PROFILE_INVALID", "fail",
                  "profile must be 'minimal' or 'full'", "profile")
        profile = None
    _validate_profile(data, profile, diags)
    _validate_evidence(data, diags)
    return not diags.has("fail")


def _validate_mode(data: dict, mode, diags: _Diags) -> None:
    process = data.get("process")
    if "process" in data:
        if not isinstance(process, dict):
            diags.add("REPORT_FIELD_TYPE", "fail", "process must be an object", "process")
            process = None
        else:
            _check_keys(process, _PROCESS_KEYS, "process", diags)
            for key in _PROCESS_KEYS:
                if key in process and not isinstance(process[key], bool):
                    diags.add("REPORT_FIELD_TYPE", "fail", "%s must be true or false" % key,
                              "process.%s" % key)
    if mode in _NO_PLAN_NO_SPEC_MODES:
        # B3a/B3b: /spec is not invoked and no SPEC is created in modes 2 to 5.
        if process is None:
            if "process" not in data:
                diags.add("REPORT_FIELD_MISSING", "fail",
                          "mode %s must state whether /spec was invoked and a SPEC or plan "
                          "created" % mode, "process")
        else:
            for key in _PROCESS_KEYS:
                if key not in process:
                    diags.add("REPORT_FIELD_MISSING", "fail", "required field %r is missing" % key,
                              "process")
            if process.get("spec_skill_invoked") is True:
                diags.add("REPORT_MODE_CONTRADICTION", "fail",
                          "mode %s works without /spec but the report says it was invoked" % mode,
                          "process.spec_skill_invoked")
            if process.get("spec_created") is True:
                diags.add("REPORT_MODE_CONTRADICTION", "fail",
                          "mode %s works without a SPEC but the report says one was created" % mode,
                          "process.spec_created")
            if mode in _NO_PLAN_MODES and process.get("plan_created") is True:
                diags.add("REPORT_MODE_CONTRADICTION", "fail",
                          "mode %s needs no plan but the report says one was created" % mode,
                          "process.plan_created")
    # B3a: in mode 2 a plan artifact exists and organises the work. In every other mode a
    # plan is not required, but an existing one may be referenced (a follow-up reusing an
    # earlier plan is legitimate); it is then described the same way.
    if mode == "plan_driven" and not _require(data, "plan", "plan", diags):
        return
    if "plan" not in data:
        return
    plan = data["plan"]
    if not isinstance(plan, dict):
        diags.add("REPORT_FIELD_TYPE", "fail", "plan must be an object", "plan")
        return
    _check_keys(plan, ("reference", "used"), "plan", diags)
    if not _require(plan, "reference", "plan", diags) or not _nonblank(plan["reference"]):
        if "reference" in plan:
            diags.add("REPORT_FIELD_EMPTY", "fail", "plan.reference must name the plan "
                      "artifact", "plan.reference")
    if _require(plan, "used", "plan", diags) and plan["used"] is not True:
        diags.add("REPORT_MODE_CONTRADICTION", "fail",
                  "a plan block states a plan that organised or informed the work; plan.used "
                  "must be true (omit the block when no plan was used)", "plan.used")


def _validate_profile(data: dict, profile, diags: _Diags) -> None:
    if profile == "minimal":
        if "categories" in data:
            diags.add("REPORT_PROFILE_CONTRADICTION", "fail",
                      "a minimal report carries 'minimal', not 'categories'", "categories")
        if not _require(data, "minimal", "minimal", diags):
            return
        block = data["minimal"]
        if not isinstance(block, dict):
            diags.add("REPORT_FIELD_TYPE", "fail", "minimal must be an object", "minimal")
            return
        _check_keys(block, ("affected_paths", "reason", "evidence"), "minimal", diags)
        if _require(block, "affected_paths", "minimal", diags):
            _paths(block["affected_paths"], "minimal.affected_paths", diags, allow_dot=False)
        if _require(block, "reason", "minimal", diags) and not _nonblank(block["reason"]):
            diags.add("REPORT_FIELD_EMPTY", "fail",
                      "reason a full category review is not required must be stated",
                      "minimal.reason")
        if _require(block, "evidence", "minimal", diags):
            _evidence_ids(block["evidence"], "minimal.evidence", diags)
    elif profile == "full":
        if "minimal" in data:
            diags.add("REPORT_PROFILE_CONTRADICTION", "fail",
                      "a full report carries 'categories', not 'minimal'", "minimal")
        if not _require(data, "categories", "categories", diags):
            return
        block = data["categories"]
        if not isinstance(block, dict):
            diags.add("REPORT_FIELD_TYPE", "fail", "categories must be an object", "categories")
            return
        _check_keys(block, CATEGORIES, "categories", diags)
        for name in CATEGORIES:
            where = "categories.%s" % name
            if name not in block:
                diags.add("REPORT_CATEGORY_MISSING", "fail",
                          "category %r has neither affected paths nor a reason for no impact" % name,
                          where)
                continue
            entry = block[name]
            if not isinstance(entry, dict):
                diags.add("REPORT_FIELD_TYPE", "fail", "category entry must be an object", where)
                continue
            _check_keys(entry, ("affected_paths", "no_impact_reason", "evidence"), where, diags)
            has_paths, has_reason = "affected_paths" in entry, "no_impact_reason" in entry
            if has_paths == has_reason:
                diags.add("REPORT_CATEGORY_INVALID", "fail",
                          "state exactly one of affected_paths or no_impact_reason", where)
            if has_paths:
                _paths(entry["affected_paths"], where + ".affected_paths", diags, allow_dot=False)
            if has_reason and not _nonblank(entry["no_impact_reason"]):
                diags.add("REPORT_FIELD_EMPTY", "fail", "no_impact_reason must be stated",
                          where + ".no_impact_reason")
            if _require(entry, "evidence", where, diags):
                _evidence_ids(entry["evidence"], where + ".evidence", diags)


def _validate_evidence(data: dict, diags: _Diags) -> None:
    if not _require(data, "evidence", "evidence", diags):
        return
    items = data["evidence"]
    if not isinstance(items, list) or not items:
        diags.add("REPORT_FIELD_TYPE", "fail", "evidence must be a non-empty list", "evidence")
        return
    seen = set()
    for index, item in enumerate(items):
        where = "evidence[%d]" % index
        if not isinstance(item, dict):
            diags.add("REPORT_FIELD_TYPE", "fail", "evidence entry must be an object", where)
            continue
        kind = item.get("kind")
        allowed = _EVIDENCE_KEYS + (("command", "tests", "exit_code") if kind == "test" else ())
        _check_keys(item, allowed, where, diags)
        if _require(item, "id", where, diags):
            ident = item["id"]
            if not (isinstance(ident, str) and _ID_RE.fullmatch(ident)):
                diags.add("REPORT_FIELD_TYPE", "fail", "id must match [A-Za-z0-9._-]{1,64}", where + ".id")
            elif ident in seen:
                diags.add("REPORT_EVIDENCE_DUPLICATE", "fail", "evidence id %r is used twice" % ident,
                          where + ".id")
            else:
                seen.add(ident)
        if _require(item, "kind", where, diags) and kind not in EVIDENCE_KINDS:
            diags.add("REPORT_EVIDENCE_KIND", "fail",
                      "kind must be one of %s (a commit message or a trailer is not evidence)"
                      % ", ".join(EVIDENCE_KINDS), where + ".kind")
        if _require(item, "tree", where, diags):
            value = item["tree"]
            if not (isinstance(value, str) and _OID_RE.fullmatch(value)):
                diags.add("REPORT_IDENTITY_INVALID", "fail",
                          "tree must be the full lowercase hexadecimal OID of the tree the "
                          "evidence was gathered on", where + ".tree")
        if _require(item, "scope_paths", where, diags):
            _paths(item["scope_paths"], where + ".scope_paths", diags, allow_dot=True)
        outcome = item.get("outcome")
        if _require(item, "outcome", where, diags) and outcome not in OUTCOMES:
            diags.add("REPORT_EVIDENCE_OUTCOME", "fail",
                      "outcome must be one of %s" % ", ".join(OUTCOMES), where + ".outcome")
        if _require(item, "summary", where, diags) and not _nonblank(item["summary"]):
            diags.add("REPORT_FIELD_EMPTY", "fail", "summary must say what was examined",
                      where + ".summary")
        if kind == "test":
            _validate_test_evidence(item, outcome, where, diags)
    if isinstance(data.get("evidence"), list):
        _validate_references(data, seen, diags)


def _validate_test_evidence(item: dict, outcome, where: str, diags: _Diags) -> None:
    if _require(item, "command", where, diags) and not _nonblank(item["command"]):
        diags.add("REPORT_FIELD_EMPTY", "fail", "command must state what was run", where + ".command")
    exit_code = item.get("exit_code")
    if "exit_code" in item and not _is_int(exit_code):
        diags.add("REPORT_FIELD_TYPE", "fail", "exit_code must be an integer", where + ".exit_code")
        exit_code = None
    if not _require(item, "tests", where, diags):
        return  # an exit code alone never establishes what ran
    tests = item["tests"]
    if not isinstance(tests, dict):
        diags.add("REPORT_FIELD_TYPE", "fail", "tests must be an object", where + ".tests")
        return
    _check_keys(tests, _TEST_KEYS, where + ".tests", diags)
    counts = {}
    for key in _TEST_KEYS:
        if _require(tests, key, where + ".tests", diags):
            if _is_int(tests[key]) and tests[key] >= 0:
                counts[key] = tests[key]
            else:
                diags.add("REPORT_FIELD_TYPE", "fail", "%s must be a non-negative integer" % key,
                          "%s.tests.%s" % (where, key))
    if len(counts) != 4:
        return
    if counts["passed"] + counts["failed"] + counts["skipped"] != counts["run"]:
        diags.add("REPORT_EVIDENCE_INCONSISTENT", "fail",
                  "passed + failed + skipped must equal run", where + ".tests")
    clean = (counts["run"] > 0 and counts["passed"] == counts["run"]
             and exit_code in (None, 0))
    if outcome == "pass" and not clean:
        diags.add("REPORT_EVIDENCE_INCONSISTENT", "fail",
                  "outcome 'pass' needs run > 0, every test passed, none failed or skipped, and "
                  "exit code 0 when given", where + ".outcome")
    if outcome == "fail" and counts["failed"] == 0 and exit_code in (None, 0):
        diags.add("REPORT_EVIDENCE_INCONSISTENT", "fail",
                  "outcome 'fail' needs a failed test or a non-zero exit code", where + ".outcome")


def _portions_of(data: dict) -> List[Tuple[str, dict]]:
    if data.get("profile") == "minimal":
        return [("minimal", data["minimal"])]
    return [(name, data["categories"][name]) for name in CATEGORIES]


def _validate_references(data: dict, known: set, diags: _Diags) -> None:
    block = data.get("minimal") if data.get("profile") == "minimal" else data.get("categories")
    if not isinstance(block, dict):
        return
    entries = [("minimal", block)] if data.get("profile") == "minimal" else [
        (name, block.get(name)) for name in CATEGORIES]
    for name, entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("evidence"), list):
            continue
        for ident in entry["evidence"]:
            if isinstance(ident, str) and ident not in known:
                diags.add("REPORT_EVIDENCE_UNKNOWN", "fail",
                          "evidence id %r is not defined in the evidence list" % ident,
                          "%s.evidence" % name)


# --- assessment against a snapshot ------------------------------------------

def _entry_map(entries) -> Dict[str, Tuple[str, str]]:
    """path -> (mode, oid). Callers pass merged entries only: commit and tree listings
    are always stage 0, and an index with unmerged entries is rejected before this."""
    return {e.path: (e.mode, e.oid) for e in entries}


def _changed_paths(old: Dict[str, Tuple[str, str]], new: Dict[str, Tuple[str, str]]) -> List[str]:
    return sorted(p for p in set(old) | set(new) if old.get(p) != new.get(p))


def _sample(paths: List[str], limit: int = 5) -> str:
    shown = ", ".join(paths[:limit])
    return shown + (" (+%d more)" % (len(paths) - limit) if len(paths) > limit else "")


def _dimension(parsed: ParsedReport, status: str, reason: str, diags: List[dict], *,
               mode=None, profile=None, subject=None, base=None, portions=None) -> dict:
    return {
        "assessed": True,
        "status": status,
        "reason": reason,
        "validated": list(VALIDATED),
        "semantic_review": "not established",
        "report": {"source": parsed.source, "path": parsed.path, "sha256": parsed.sha256,
                   "bytes": parsed.size},
        "mode": mode,
        "profile": profile,
        "subject": subject or {"tree": None, "snapshot_tree": None, "matches": None},
        "base": base or {"tree": None, "verification": "none", "independently_verified": False,
                         "changed_paths": None},
        "portions": portions or [],
        "diagnostics": diags,
    }


def not_assessed(reason: str) -> dict:
    return {"assessed": False, "reason": reason}


def assess(parsed: ParsedReport, snapshot: Optional[Snapshot], remote_oid: Optional[str],
           spec_tracks: Optional[Callable[[Snapshot], Sequence[str]]] = None) -> dict:
    """Validate `parsed` against one snapshot. GitError propagates (tool failure).
    A malformed report needs no snapshot, so `snapshot` may be None for it.
    `spec_tracks(snapshot)` returns the registered SPEC track paths of a snapshot (the same
    rule as the SPEC check) or raises SpecPolicyUnreadable; only registered tracks count as
    "a SPEC" for the no-SPEC-created comparison (ADR-0014 Decision 2)."""
    if parsed.data is None:
        return _dimension(parsed, FAIL, "the impact report is malformed or incomplete; no "
                          "assessment is established", parsed.diagnostics)
    data = parsed.data
    diags = _Diags()
    mode, profile = data["mode"], data["profile"]
    repo = snapshot.repo
    reported_subject, reported_base = data["subject_tree"], data["base_tree"]

    # -- identity: subject
    if snapshot.kind == "commit":
        actual = snapshot.identity["tree"]
    else:
        actual = gitstate.index_tree_oid(snapshot.entries, len(reported_subject))
        if actual is None:
            diags.add("SUBJECT_TREE_UNAVAILABLE", "fail",
                      "the index holds unmerged entries and has no tree to bind the report to",
                      "subject_tree")
    subject = {"tree": reported_subject, "snapshot_tree": actual, "matches": None}
    if actual is not None:
        subject["matches"] = actual == reported_subject
        if actual != reported_subject:
            diags.add("SUBJECT_MISMATCH", "fail",
                      "the report reviewed tree %s but the snapshot under check is tree %s"
                      % (reported_subject, actual), "subject_tree")

    # -- identity: base
    base = {"tree": reported_base, "verification": "none", "independently_verified": False,
            "changed_paths": None}
    base_type = gitstate.object_type(repo, reported_base)
    base_entries = None
    if base_type is None:
        diags.add("BASE_NOT_AVAILABLE", "skip",
                  "the base tree %s is not in this repository, so the changes cannot be "
                  "established" % reported_base, "base_tree")
    elif base_type != "tree":
        diags.add("BASE_NOT_A_TREE", "fail",
                  "base_tree %s names a %s, not a tree" % (reported_base, base_type), "base_tree")
    else:
        base_entries = gitstate.list_tree(repo, reported_base)
    if remote_oid and not gitstate.is_zero_oid(remote_oid):
        tip_tree = (gitstate.commit_tree_oid(repo, remote_oid)
                    if gitstate.is_oid(remote_oid) else None)
        if tip_tree is None:
            base["verification"] = "unavailable"
            diags.add("BASE_NOT_VERIFIABLE", "skip",
                      "the old tip %r of the pushed ref is not a commit in this repository, so "
                      "base_tree cannot be checked against it" % remote_oid[:80], "base_tree")
        elif tip_tree != reported_base:
            base["verification"] = "push-remote-tip"
            diags.add("BASE_MISMATCH", "fail",
                      "base_tree %s is not the tree of the old tip %s (tree %s)"
                      % (reported_base, remote_oid, tip_tree), "base_tree")
        else:
            base["verification"] = "push-remote-tip"
            base["independently_verified"] = True
    else:
        diags.add("BASE_NOT_INDEPENDENTLY_VERIFIED", "note",
                  "this invocation supplies no old tip, so base_tree is recorded as reported and "
                  "was not checked against an independently supplied base", "base_tree")

    if diags.has("fail") or base_entries is None:
        return _finish(parsed, diags, mode, profile, subject, base, [])

    # -- completeness
    subject_map = _entry_map(snapshot.entries)
    base_map = _entry_map(base_entries)
    changed = _changed_paths(base_map, subject_map)
    base["changed_paths"] = len(changed)
    portions = _portions_of(data)
    affected = [p for _, entry in portions for p in _entry_paths(entry)]
    uncovered = [p for p in changed if not any(covers(a, p) for a in affected)]
    if uncovered:
        diags.add("CHANGED_PATH_NOT_REPORTED", "skip",
                  "%d changed path(s) between base and subject are named by no affected_paths: %s"
                  % (len(uncovered), _sample(uncovered)), "affected_paths")
    process = data.get("process")
    if spec_tracks and isinstance(process, dict) and process.get("spec_created") is False:
        # The report states that no SPEC was created, whatever its mode (modes 2 to 5 must
        # state it; a mode 1 report may). The base policy is needed to classify paths; if it
        # cannot be read the classification is unknown and the assessment stops, incomplete.
        try:
            base_tracks = spec_tracks(Snapshot("commit", {"commit": None, "tree": reported_base,
                                                          "index_digest": None},
                                               base_entries, repo))
            subject_tracks = spec_tracks(snapshot)
        except SpecPolicyUnreadable as exc:
            diags.add("SPEC_POLICY_UNREADABLE", "skip",
                      "%s; which paths were SPEC tracks cannot be established, so whether a SPEC "
                      "was created cannot be checked" % exc, "process.spec_created")
            return _finish(parsed, diags, mode, profile, subject, base, [])
        created = _created_spec_tracks(subject_tracks, base_tracks, subject_map, base_map)
        if created:
            diags.add("SPEC_CREATED_CONTRADICTS_REPORT", "fail",
                      "the report says no SPEC was created, but %s is a SPEC track that is new "
                      "in the change" % _sample(created), "process.spec_created")

    # -- freshness
    evidence = {item["id"]: item for item in data["evidence"]}
    cited = {ident for _, entry in portions for ident in entry["evidence"]}
    uncited = sorted(set(evidence) - cited)
    if uncited:
        diags.add("EVIDENCE_NOT_CITED", "note",
                  "evidence %s is cited by no portion and was not assessed" % ", ".join(uncited),
                  "evidence")
    # A recorded failure is not hidden by leaving it uncited: if it is current, it fails.
    failures = {ident: evidence[ident] for ident in uncited if evidence[ident]["outcome"] == "fail"}
    for ident, state in _evidence_freshness(repo, failures, reported_subject, subject_map,
                                            diags).items():
        if state["freshness"] == "fresh":
            diags.add("UNCITED_EVIDENCE_RECORDS_FAILURE", "fail",
                      "evidence %r records a failure on a current tree but no portion cites it"
                      % ident, "evidence")
    freshness = _evidence_freshness(
        repo, {ident: evidence[ident] for ident in evidence if ident in cited},
        reported_subject, subject_map, diags)
    results = []
    for name, entry in portions:
        results.append(_portion(name, entry, evidence, freshness, changed, diags))
    return _finish(parsed, diags, mode, profile, subject, base, results)


def _created_spec_tracks(subject_tracks: Sequence[str], base_tracks: Sequence[str],
                         subject_map: dict, base_map: dict) -> List[str]:
    """SPEC paths that are created by the change, decided by path and registration alone
    (ADR-0014 Decision 2). Candidates are every path registered as a SPEC track in the base
    policy or in the subject policy. A candidate that has a file in the subject is created
    if it was not registered in the base, or if no file existed at it in the base. Nothing
    is correlated between paths: a SPEC relocated to a new path is created there, and
    removing a registration while a file appears at that path is still seen."""
    registered_in_base = set(base_tracks)
    return sorted(
        path for path in set(subject_tracks) | registered_in_base
        if path in subject_map and (path not in registered_in_base or path not in base_map))


def _entry_paths(entry: dict) -> List[str]:
    return [_norm(p) for p in entry.get("affected_paths", [])]


def _evidence_freshness(repo, evidence: dict, subject_tree: str, subject_map: dict,
                        diags: _Diags) -> Dict[str, dict]:
    """fresh: nothing in the evidence scope differs between its tree and the subject.
    stale: a path in scope differs. unverifiable: its tree is not in this repository.
    invalid: it names an object that is not a tree."""
    out = {}
    maps: Dict[str, Optional[dict]] = {}
    for ident, item in evidence.items():
        tree = item["tree"]
        scopes = [_norm(s) for s in item["scope_paths"]]
        if tree == subject_tree:
            out[ident] = {"freshness": "fresh", "reason": "gathered on the subject tree"}
            continue
        if tree not in maps:
            kind = gitstate.object_type(repo, tree)
            maps[tree] = _entry_map(gitstate.list_tree(repo, tree)) if kind == "tree" else None
            if kind is not None and kind != "tree":
                diags.add("EVIDENCE_TREE_NOT_A_TREE", "fail",
                          "evidence %r names %s, a %s, not a tree" % (ident, tree, kind),
                          "evidence")
                maps[tree] = False  # type: ignore[assignment]
        old = maps[tree]
        if old is False:
            out[ident] = {"freshness": "invalid", "reason": "its tree is not a tree object"}
        elif old is None:
            out[ident] = {"freshness": "unverifiable",
                          "reason": "its tree %s is not in this repository" % tree}
            diags.add("EVIDENCE_TREE_NOT_AVAILABLE", "skip",
                      "evidence %r was gathered on tree %s, which is not in this repository, so "
                      "its freshness cannot be established" % (ident, tree), "evidence")
        else:
            moved = [p for p in _changed_paths(old, subject_map)
                     if any(covers(s, p) for s in scopes)]
            if moved:
                out[ident] = {"freshness": "stale",
                              "reason": "%d path(s) in its scope changed since its tree: %s"
                              % (len(moved), _sample(moved))}
            else:
                out[ident] = {"freshness": "fresh",
                              "reason": "nothing in its scope changed since its tree"}
    return out


def _portion(name: str, entry: dict, evidence: dict, freshness: Dict[str, dict],
             changed: List[str], diags: _Diags) -> dict:
    refs = [evidence[i] for i in entry["evidence"]]
    status, reasons = PASS, []

    def worsen(new: str, why: str) -> None:
        nonlocal status
        reasons.append(why)
        if new == FAIL or (new == SKIP and status == PASS):
            status = new

    scopes = [_norm(s) for item in refs for s in item["scope_paths"]]
    required = _entry_paths(entry) or changed  # a no-impact claim must cover the whole change
    gap = [p for p in required if not any(covers(s, p) for s in scopes)]
    if gap:
        diags.add("EVIDENCE_SCOPE_GAP", "skip",
                  "%s: no cited evidence scope covers %d path(s): %s" % (name, len(gap), _sample(gap)),
                  name)
        worsen(SKIP, "evidence scope does not cover %d path(s)" % len(gap))
    listed = []
    for item in refs:
        fresh = freshness[item["id"]]
        listed.append({"id": item["id"], "freshness": fresh["freshness"],
                       "outcome": item["outcome"], "reason": fresh["reason"]})
        if fresh["freshness"] == "invalid":
            worsen(FAIL, "evidence %s names an object that is not a tree" % item["id"])
        elif fresh["freshness"] != "fresh":
            worsen(SKIP, "evidence %s is %s: renewed review required" % (item["id"], fresh["freshness"]))
        elif item["outcome"] == "fail":
            worsen(FAIL, "evidence %s records a failure" % item["id"])
        elif item["outcome"] in ("partial", "skipped"):
            worsen(SKIP, "evidence %s is %s" % (item["id"], item["outcome"]))
    return {"name": name, "status": status,
            "reason": "; ".join(reasons) if reasons else "cited evidence is fresh and passed",
            "evidence": listed}


def _finish(parsed: ParsedReport, diags: _Diags, mode, profile, subject, base,
            portions: List[dict]) -> dict:
    items = diags.finish()
    statuses = [p["status"] for p in portions]
    if diags.has("fail") or FAIL in statuses:
        status = FAIL
        reason = ("the report is inconsistent with the snapshot or records a failure; see "
                  "diagnostics and portions")
    elif diags.has("skip") or SKIP in statuses:
        status = SKIP
        reason = ("the assessment is incomplete, stale or not verifiable; renewed review of the "
                  "affected portions is required")
    else:
        status = PASS
        reason = ("report structure, snapshot binding, completeness and evidence freshness are "
                  "established; whether its statements are true is not")
        if not base["independently_verified"]:
            reason = ("report structure, snapshot binding, completeness relative to the reported "
                      "base, and evidence freshness are established; the base was not "
                      "independently verified; whether its statements are true is not established")
    return _dimension(parsed, status, reason, items, mode=mode, profile=profile,
                      subject=subject, base=base, portions=portions)
