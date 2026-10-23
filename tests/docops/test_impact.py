"""DOC_IMPACT_ASSESSMENT: the impact-report validator (ADR-0014).

What these tests establish is deliberately narrow, because that is all the
validator establishes: report structure, binding to the snapshot under check,
completeness against the real base-to-subject change, and evidence
freshness. Nothing here asserts that a report is *true*; one test shows that
a complete but false report passes, and states why that is the intended
limit.

Criteria mapping (Stage 0 plan, table G): B3/B3a/B3b -> ModeTests,
B4 -> ProfileTests, B5 -> FreshnessTests, identity and result contract ->
IdentityTests, ResultContractTests. Sections H and I of those tests state
which assertion proves which claim.
"""
import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from helpers import (DocopsTestCase, GitFixture, clean_env, load_impact_schema,
                     run_check_json, run_cli, tree_digest, validate)

from docops_core import checks, gitstate, impact
from docops_core.gitstate import GitError

ZERO = "0" * 40
ALL_MODES = ("spec_driven", "plan_driven", "audit_test_driven", "direct_fix", "follow_up")
CATEGORIES = impact.CATEGORIES


# --- builders ----------------------------------------------------------------

def mode_fields(mode):
    if mode == "spec_driven":
        return {}
    fields = {"process": {"spec_skill_invoked": False, "spec_created": False,
                          "plan_created": False}}
    if mode == "plan_driven":
        fields["plan"] = {"reference": "plans/stage-x.md", "used": True}
    return fields


def review(ident="e1", tree="a" * 40, scope=(".",), outcome="pass", summary="read the change"):
    return {"id": ident, "kind": "review", "tree": tree, "scope_paths": list(scope),
            "outcome": outcome, "summary": summary}


def tests_evidence(ident="t1", tree="a" * 40, scope=(".",), outcome="pass", run=10, passed=10,
                  failed=0, skipped=0, exit_code=0, command="python3 -m unittest"):
    item = {"id": ident, "kind": "test", "tree": tree, "scope_paths": list(scope),
            "outcome": outcome, "summary": "unit suite", "command": command,
            "tests": {"run": run, "passed": passed, "failed": failed, "skipped": skipped}}
    if exit_code is not None:
        item["exit_code"] = exit_code
    return item


def minimal_report(base, subject, paths, mode="direct_fix", evidence=None, cited=None,
                   reason="one line changed; no category is affected"):
    evidence = evidence if evidence is not None else [review("e1", subject)]
    return {"schema_version": 1, "mode": mode, **mode_fields(mode),
            "base_tree": base, "subject_tree": subject, "profile": "minimal",
            "minimal": {"affected_paths": list(paths), "reason": reason,
                        "evidence": cited or [e["id"] for e in evidence]},
            "evidence": evidence}


def full_report(base, subject, affected=None, mode="direct_fix", evidence=None, cited=None):
    """All eight categories; `affected` maps a category to its paths, the rest say no impact."""
    evidence = evidence if evidence is not None else [review("e1", subject)]
    ids = cited or [e["id"] for e in evidence]
    categories = {}
    for name in CATEGORIES:
        if affected and name in affected:
            categories[name] = {"affected_paths": list(affected[name]), "evidence": ids}
        else:
            categories[name] = {"no_impact_reason": "nothing in %s depends on this change" % name,
                                "evidence": ids}
    return {"schema_version": 1, "mode": mode, **mode_fields(mode),
            "base_tree": base, "subject_tree": subject, "profile": "full",
            "categories": categories, "evidence": evidence}


def encode(report):
    return report if isinstance(report, bytes) else json.dumps(report).encode("utf-8")


def parse(report):
    return impact.load_report(encode(report), "stdin", None)


def codes(diagnostics, effect=None):
    return sorted(d["code"] for d in diagnostics if effect is None or d["effect"] == effect)


# --- fixture repository --------------------------------------------------------

class ImpactCase(DocopsTestCase):
    """History: A (base) -> B (src/a.py and docs/a.md edited) on branch main."""

    def setUp(self):
        self.fx = GitFixture()
        self.addCleanup(self.fx.cleanup)
        self.A = self.fx.commit_files({"README.md": "r\n", "src/a.py": "a = 1\n",
                                       "docs/a.md": "doc a\n", "docs2/x.md": "x\n"}, "A")
        self.fx.stage("src/a.py", "a = 2\n")
        self.fx.stage("docs/a.md", "doc a v2\n")
        self.B = self.fx.commit("B")
        self.tA, self.tB = self.tree(self.A), self.tree(self.B)

    def tree(self, rev):
        return self.fx.git("rev-parse", rev + "^{tree}")

    def commit_change(self, files, message="more", remove=()):
        for rel in remove:
            self.fx.git("rm", "-q", "--", rel)
        for rel, content in files.items():
            self.fx.stage(rel, content)
        return self.fx.commit(message)

    def assess(self, report, rev="HEAD", remote_oid=None):
        """In-process check of one snapshot; returns the whole snapshot result."""
        repo = gitstate.open_repository(self.fx.root)
        request = {"kind": "index"} if rev == "index" else {"kind": "rev", "rev": rev}
        if remote_oid is not None:
            request["remote_oid"] = remote_oid
        return checks.check_request(repo, request, parse(report))

    def dim(self, report, rev="HEAD", remote_oid=None):
        result = self.assess(report, rev, remote_oid)
        self.assertIsNone(result["error"], result["error"])
        dimension = result["checks"]["DOC_IMPACT_ASSESSMENT"]
        self.assertTrue(dimension["assessed"])
        return dimension

    def cli(self, report, *args, extra=()):
        return run_check_json(["--repo", self.fx.root, *args, "--impact-file", "-", *extra],
                              stdin=encode(report))

    def good_minimal(self, subject=None, **kw):
        subject = subject or self.tB
        return minimal_report(self.tA, subject, ["src/a.py", "docs/a.md"],
                              evidence=kw.pop("evidence", None) or [review("e1", subject)], **kw)


# --- A. structure --------------------------------------------------------------

def _set(path, value):
    """Return a mutator that sets/removes (value is DELETE) a dotted path in a report."""
    def apply(report):
        node = report
        keys = path.split(".")
        for key in keys[:-1]:
            node = node[int(key)] if isinstance(node, list) else node[key]
        last = keys[-1]
        if value is DELETE:
            del node[int(last) if isinstance(node, list) else last]
        else:
            node[int(last) if isinstance(node, list) else last] = value
    return apply


DELETE = object()
T40, T64 = "c" * 40, "d" * 64


def _base_minimal(mode="direct_fix"):
    return minimal_report("a" * 40, "b" * 40, ["src/a.py"], mode=mode,
                          evidence=[review("e1", "b" * 40)])


def _base_full(mode="direct_fix"):
    return full_report("a" * 40, "b" * 40, {"architecture": ["src/a.py"]}, mode=mode,
                       evidence=[review("e1", "b" * 40)])


# (name, base builder, mutations, schema_can_express)
STRUCTURE_REJECTED = [
    ("schema_version 2", _base_minimal, [_set("schema_version", 2)], True),
    ("schema_version as string", _base_minimal, [_set("schema_version", "1")], True),
    ("schema_version as true", _base_minimal, [_set("schema_version", True)], True),
    ("schema_version missing", _base_minimal, [_set("schema_version", DELETE)], True),
    ("mode missing", _base_minimal, [_set("mode", DELETE)], True),
    ("mode unknown", _base_minimal, [_set("mode", "yolo")], True),
    ("mode a number", _base_minimal, [_set("mode", 2)], True),
    ("mode a list", _base_minimal, [_set("mode", ["direct_fix"])], True),
    ("profile missing", _base_minimal, [_set("profile", DELETE)], True),
    ("profile unknown", _base_minimal, [_set("profile", "medium")], True),
    ("minimal block missing", _base_minimal, [_set("minimal", DELETE)], True),
    ("minimal profile with categories", _base_minimal,
     [_set("categories", _base_full()["categories"])], True),
    ("full profile with minimal block", _base_full,
     [_set("minimal", _base_minimal()["minimal"])], True),
    ("categories block missing", _base_full, [_set("categories", DELETE)], True),
    ("minimal reason missing", _base_minimal, [_set("minimal.reason", DELETE)], True),
    ("minimal reason blank", _base_minimal, [_set("minimal.reason", "  \t")], True),
    ("minimal affected_paths empty", _base_minimal, [_set("minimal.affected_paths", [])], True),
    ("minimal affected_paths missing", _base_minimal,
     [_set("minimal.affected_paths", DELETE)], True),
    ("minimal evidence not cited", _base_minimal, [_set("minimal.evidence", [])], True),
    ("unknown top-level field closes", _base_minimal, [_set("closes", "B-001")], True),
    ("unknown minimal field", _base_minimal, [_set("minimal.size", "small")], True),
    ("base_tree missing", _base_minimal, [_set("base_tree", DELETE)], True),
    ("base_tree too short", _base_minimal, [_set("base_tree", "abc123")], True),
    ("base_tree uppercase", _base_minimal, [_set("base_tree", "A" * 40)], True),
    ("base_tree 41 digits", _base_minimal, [_set("base_tree", "a" * 41)], True),
    ("base_tree not a string", _base_minimal, [_set("base_tree", 12345)], True),
    ("base_tree null", _base_minimal, [_set("base_tree", None)], True),
    ("subject_tree missing", _base_minimal, [_set("subject_tree", DELETE)], True),
    ("subject_tree a branch name", _base_minimal, [_set("subject_tree", "main")], True),
    ("plan_driven without plan", lambda: _base_minimal("plan_driven"),
     [_set("plan", DELETE)], True),
    ("plan not used", lambda: _base_minimal("plan_driven"), [_set("plan.used", False)], True),
    ("plan used as string", lambda: _base_minimal("plan_driven"),
     [_set("plan.used", "yes")], True),
    ("plan reference blank", lambda: _base_minimal("plan_driven"),
     [_set("plan.reference", " ")], True),
    ("plan reference missing", lambda: _base_minimal("plan_driven"),
     [_set("plan.reference", DELETE)], True),
    ("plan with extra field", lambda: _base_minimal("plan_driven"),
     [_set("plan.owner", "me")], True),
    ("plan block that was not used, in direct_fix", lambda: _base_minimal("direct_fix"),
     [_set("plan", {"reference": "p.md", "used": False})], True),
    ("plan block without a reference, in follow_up", lambda: _base_minimal("follow_up"),
     [_set("plan", {"used": True})], True),
    ("plan block that is not an object, in spec_driven", lambda: _base_minimal("spec_driven"),
     [_set("plan", "p.md")], True),
    ("plan block with an extra field, in audit_test_driven",
     lambda: _base_minimal("audit_test_driven"),
     [_set("plan", {"reference": "p.md", "used": True, "owner": "me"})], True),
    ("base equals subject", _base_minimal, [_set("subject_tree", "a" * 40)], False),
    ("base equals subject, base_tree altered", _base_minimal, [_set("base_tree", "b" * 40)], False),
    ("base equals subject (64 digits)", _base_minimal,
     [_set("base_tree", T64), _set("subject_tree", T64), _set("evidence.0.tree", T64)], False),
    ("process missing in plan_driven", lambda: _base_minimal("plan_driven"),
     [_set("process", DELETE)], True),
    ("process missing in audit_test_driven", lambda: _base_minimal("audit_test_driven"),
     [_set("process", DELETE)], True),
    ("process missing in direct_fix", lambda: _base_minimal("direct_fix"),
     [_set("process", DELETE)], True),
    ("process missing in follow_up", lambda: _base_minimal("follow_up"),
     [_set("process", DELETE)], True),
    ("/spec invoked in plan_driven", lambda: _base_minimal("plan_driven"),
     [_set("process.spec_skill_invoked", True)], True),
    ("/spec invoked in audit_test_driven", lambda: _base_minimal("audit_test_driven"),
     [_set("process.spec_skill_invoked", True)], True),
    ("/spec invoked in direct_fix", lambda: _base_minimal("direct_fix"),
     [_set("process.spec_skill_invoked", True)], True),
    ("/spec invoked in follow_up", lambda: _base_minimal("follow_up"),
     [_set("process.spec_skill_invoked", True)], True),
    ("SPEC created in plan_driven", lambda: _base_minimal("plan_driven"),
     [_set("process.spec_created", True)], True),
    ("SPEC created in direct_fix", lambda: _base_minimal("direct_fix"),
     [_set("process.spec_created", True)], True),
    ("plan created in audit_test_driven", lambda: _base_minimal("audit_test_driven"),
     [_set("process.plan_created", True)], True),
    ("plan created in direct_fix", lambda: _base_minimal("direct_fix"),
     [_set("process.plan_created", True)], True),
    ("plan created in follow_up", lambda: _base_minimal("follow_up"),
     [_set("process.plan_created", True)], True),
    ("process key missing", lambda: _base_minimal("direct_fix"),
     [_set("process.spec_created", DELETE)], True),
    ("process value not boolean", lambda: _base_minimal("direct_fix"),
     [_set("process.spec_created", 0)], True),
    ("process unknown key", lambda: _base_minimal("direct_fix"),
     [_set("process.extra", False)], True),
    ("full: a category missing", _base_full, [_set("categories.tests", DELETE)], True),
    ("full: another category missing", _base_full,
     [_set("categories.planning_and_operations", DELETE)], True),
    ("full: unknown category", _base_full,
     [_set("categories.performance", {"no_impact_reason": "x", "evidence": ["e1"]})], True),
    ("full: category with neither field", _base_full,
     [_set("categories.tests", {"evidence": ["e1"]})], True),
    ("full: category with both fields", _base_full,
     [_set("categories.tests", {"affected_paths": ["t"], "no_impact_reason": "x",
                                "evidence": ["e1"]})], True),
    ("full: blank no_impact_reason", _base_full,
     [_set("categories.tests", {"no_impact_reason": "   ", "evidence": ["e1"]})], True),
    ("full: empty affected_paths", _base_full,
     [_set("categories.tests", {"affected_paths": [], "evidence": ["e1"]})], True),
    ("full: category without evidence", _base_full,
     [_set("categories.tests", {"no_impact_reason": "x"})], True),
    ("full: category evidence empty", _base_full,
     [_set("categories.tests", {"no_impact_reason": "x", "evidence": []})], True),
    ("full: category not an object", _base_full, [_set("categories.tests", "none")], True),
    ("path absolute", _base_minimal, [_set("minimal.affected_paths", ["/etc/passwd"])], True),
    ("path with ..", _base_minimal, [_set("minimal.affected_paths", ["a/../b"])], True),
    ("path with .", _base_minimal, [_set("minimal.affected_paths", ["./a"])], True),
    ("path empty component", _base_minimal, [_set("minimal.affected_paths", ["a//b"])], True),
    ("path backslash", _base_minimal, [_set("minimal.affected_paths", ["a\\b"])], True),
    ("path with newline", _base_minimal, [_set("minimal.affected_paths", ["a\nb"])], True),
    ("path with NUL", _base_minimal, [_set("minimal.affected_paths", ["a\u0000b"])], True),
    ("path empty string", _base_minimal, [_set("minimal.affected_paths", [""])], True),
    ("path dot in affected_paths", _base_minimal, [_set("minimal.affected_paths", ["."])], True),
    ("path is a number", _base_minimal, [_set("minimal.affected_paths", [5])], True),
    ("evidence missing", _base_minimal, [_set("evidence", DELETE)], True),
    ("evidence empty", _base_minimal, [_set("evidence", [])], True),
    ("evidence not a list", _base_minimal, [_set("evidence", {})], True),
    ("evidence id with space", _base_minimal, [_set("evidence.0.id", "e 1")], True),
    ("evidence kind commit_message", _base_minimal,
     [_set("evidence.0.kind", "commit_message")], True),
    ("evidence kind commit", _base_minimal, [_set("evidence.0.kind", "commit")], True),
    ("evidence tree invalid", _base_minimal, [_set("evidence.0.tree", "HEAD")], True),
    ("evidence tree missing", _base_minimal, [_set("evidence.0.tree", DELETE)], True),
    ("evidence scope empty", _base_minimal, [_set("evidence.0.scope_paths", [])], True),
    ("evidence scope missing", _base_minimal, [_set("evidence.0.scope_paths", DELETE)], True),
    ("evidence scope absolute", _base_minimal,
     [_set("evidence.0.scope_paths", ["/x"])], True),
    ("evidence outcome unknown", _base_minimal, [_set("evidence.0.outcome", "ok")], True),
    ("evidence outcome missing", _base_minimal, [_set("evidence.0.outcome", DELETE)], True),
    ("evidence summary blank", _base_minimal, [_set("evidence.0.summary", "")], True),
    ("evidence unknown field", _base_minimal, [_set("evidence.0.closes", "B-1")], True),
    ("review evidence carrying tests", _base_minimal,
     [_set("evidence.0.tests", {"run": 1, "passed": 1, "failed": 0, "skipped": 0})], True),
    ("review evidence carrying exit_code", _base_minimal,
     [_set("evidence.0.exit_code", 0)], True),
    ("test evidence without tests (exit code alone)", _base_minimal,
     [_set("evidence", [tests_evidence("e1", "b" * 40)]), _set("evidence.0.tests", DELETE)], True),
    ("test evidence without command", _base_minimal,
     [_set("evidence", [tests_evidence("e1", "b" * 40)]), _set("evidence.0.command", DELETE)], True),
    ("test counts negative", _base_minimal,
     [_set("evidence", [tests_evidence("e1", "b" * 40)]), _set("evidence.0.tests.run", -1)], True),
    ("test counts as strings", _base_minimal,
     [_set("evidence", [tests_evidence("e1", "b" * 40)]), _set("evidence.0.tests.run", "10")], True),
    ("test counts as booleans", _base_minimal,
     [_set("evidence", [tests_evidence("e1", "b" * 40)]), _set("evidence.0.tests.run", True)], True),
    ("test count missing", _base_minimal,
     [_set("evidence", [tests_evidence("e1", "b" * 40)]),
      _set("evidence.0.tests.skipped", DELETE)], True),
    ("evidence id of 65 characters, cited consistently", _base_minimal,
     [_set("evidence.0.id", "x" * 65), _set("minimal.evidence", ["x" * 65])], True),
    ("unknown field in a category entry", _base_full,
     [_set("categories.tests", {"no_impact_reason": "x", "evidence": ["e1"], "note": "n"})], True),
    ("negative counts that still add up", _base_minimal,
     [_set("evidence", [tests_evidence("e1", "b" * 40, outcome="partial", run=5, passed=7,
                                       failed=-2, exit_code=None)])], True),
    ("exit_code as string on a partial run", _base_minimal,
     [_set("evidence", [tests_evidence("e1", "b" * 40, outcome="partial", run=5, passed=5,
                                       exit_code=None)]), _set("evidence.0.exit_code", "0")], True),
    ("exit_code as string", _base_minimal,
     [_set("evidence", [tests_evidence("e1", "b" * 40)]), _set("evidence.0.exit_code", "0")], True),
    # rules that need more than one field: enforced by the checker only
    ("duplicate evidence ids", _base_minimal,
     [_set("evidence", [review("e1", "b" * 40), review("e1", "b" * 40)])], False),
    ("cited evidence id undefined", _base_minimal, [_set("minimal.evidence", ["nope"])], False),
    ("test counts do not add up", _base_minimal,
     [_set("evidence", [tests_evidence("e1", "b" * 40, run=10, passed=7)])], False),
    ("test counts exceed the run", _base_minimal,
     [_set("evidence", [tests_evidence("e1", "b" * 40, run=5, passed=5, failed=1,
                                       outcome="fail", exit_code=1)])], False),
    ("pass with zero tests run", _base_minimal,
     [_set("evidence", [tests_evidence("e1", "b" * 40, run=0, passed=0)])], False),
    ("pass with a failed test", _base_minimal,
     [_set("evidence", [tests_evidence("e1", "b" * 40, run=10, passed=9, failed=1)])], False),
    ("pass with a skipped test", _base_minimal,
     [_set("evidence", [tests_evidence("e1", "b" * 40, run=10, passed=9, skipped=1)])], False),
    ("pass with non-zero exit code", _base_minimal,
     [_set("evidence", [tests_evidence("e1", "b" * 40, exit_code=1)])], False),
    ("fail outcome without a failure", _base_minimal,
     [_set("evidence", [tests_evidence("e1", "b" * 40, outcome="fail")])], False),
    ("SPEC created in follow_up", lambda: _base_minimal("follow_up"),
     [_set("process.spec_created", True)], True),
]

STRUCTURE_ACCEPTED = [
    ("minimal, direct_fix", lambda: _base_minimal("direct_fix"), []),
    ("minimal, spec_driven without process", lambda: _base_minimal("spec_driven"), []),
    ("minimal, spec_driven with a process block", lambda: _base_minimal("spec_driven"),
     [_set("process", {"spec_skill_invoked": True, "spec_created": True, "plan_created": True})]),
    ("minimal, plan_driven", lambda: _base_minimal("plan_driven"), []),
    ("an existing plan referenced in direct_fix", lambda: _base_minimal("direct_fix"),
     [_set("plan", {"reference": "plans/earlier.md", "used": True})]),
    ("an existing plan referenced in audit_test_driven", lambda: _base_minimal("audit_test_driven"),
     [_set("plan", {"reference": "plans/earlier.md", "used": True})]),
    ("an existing plan referenced in follow_up", lambda: _base_minimal("follow_up"),
     [_set("plan", {"reference": "plans/earlier.md", "used": True})]),
    ("a plan referenced in spec_driven", lambda: _base_minimal("spec_driven"),
     [_set("plan", {"reference": "plans/earlier.md", "used": True})]),
    ("minimal, plan_driven that created its plan", lambda: _base_minimal("plan_driven"),
     [_set("process.plan_created", True)]),
    ("minimal, audit_test_driven", lambda: _base_minimal("audit_test_driven"), []),
    ("minimal, follow_up", lambda: _base_minimal("follow_up"), []),
    ("full, direct_fix", lambda: _base_full("direct_fix"), []),
    ("full, plan_driven", lambda: _base_full("plan_driven"), []),
    ("sha256 object ids", _base_minimal,
     [_set("base_tree", T64), _set("subject_tree", "e" * 64), _set("evidence.0.tree", "e" * 64)]),
    ("directory path with trailing slash", _base_minimal,
     [_set("minimal.affected_paths", ["src/", "docs/a.md"])]),
    ("whole-tree evidence scope", _base_minimal, [_set("evidence.0.scope_paths", ["."])]),
    ("test evidence, clean pass", _base_minimal, [_set("evidence", [tests_evidence("e1", "b" * 40)])]),
    ("test evidence, recorded failure", _base_minimal,
     [_set("evidence", [tests_evidence("e1", "b" * 40, outcome="fail", run=10, passed=9, failed=1,
                                      exit_code=1)])]),
    ("test evidence, partial run", _base_minimal,
     [_set("evidence", [tests_evidence("e1", "b" * 40, outcome="partial", run=5, passed=5)])]),
    ("test evidence without an exit code", _base_minimal,
     [_set("evidence", [tests_evidence("e1", "b" * 40, exit_code=None)])]),
    ("unicode summary and path", _base_minimal,
     [_set("evidence.0.summary", "проверено"), _set("minimal.affected_paths", ["docs/é.md"])]),
]


class StructureTests(unittest.TestCase):
    """Report structure, judged by the checker and, where a JSON Schema can express the
    rule, by schemas/docops-impact-report.schema.json too (they must agree)."""

    @classmethod
    def setUpClass(cls):
        cls.schema = load_impact_schema()

    def _check(self, name, build, mutations, schema_can_express, accepted):
        report = build()
        for mutate in mutations:
            mutate(report)
        parsed = parse(report)
        fails = codes(parsed.diagnostics, "fail")
        if accepted:
            self.assertEqual(fails, [], "%s: rejected: %s" % (name, parsed.diagnostics[:2]))
            self.assertIsNotNone(parsed.data)
        else:
            self.assertNotEqual(fails, [], "%s: a malformed report was accepted" % name)
            self.assertIsNone(parsed.data)
        if schema_can_express:
            problems = validate(report, self.schema)
            self.assertEqual(problems == [], accepted,
                             "%s: schema and checker disagree: %s" % (name, problems[:2]))

    def test_every_malformed_report_is_rejected_and_never_carries_data(self):
        for name, build, mutations, schema_can_express in STRUCTURE_REJECTED:
            with self.subTest(name):
                self._check(name, build, mutations, schema_can_express, accepted=False)

    def test_every_well_formed_report_is_accepted_by_checker_and_schema(self):
        for name, build, mutations in STRUCTURE_ACCEPTED:
            with self.subTest(name):
                self._check(name, build, mutations, True, accepted=True)

    def test_all_five_modes_are_represented_and_nothing_else(self):
        self.assertEqual(impact.MODES, ALL_MODES)
        self.assertEqual(self.schema["properties"]["mode"]["enum"], list(ALL_MODES))
        self.assertEqual(len(CATEGORIES), 8)
        self.assertEqual(sorted(self.schema["$defs"]["categories"]["required"]), sorted(CATEGORIES))

    def test_path_rules_agree_between_checker_and_schema(self):
        path_schema = {"$ref": "#/$defs/path"}
        scope_schema = {"$ref": "#/$defs/scopePath"}
        samples = ["a", "a/b", "a/", "dir/sub/", "a.b/c", "..x", "x..", ".hidden", "é/ü.txt",
                   "/", "/a", "", ".", "..", "./a", "a/.", "a/..", "a//b", "a\\b", "a\nb",
                   "a\x7fb", "a\x00b", "a//", " a", "a b"]
        for sample in samples:
            with self.subTest(repr(sample)):
                self.assertEqual(validate(sample, path_schema, self.schema) == [],
                                 impact.valid_path(sample, allow_dot=False))
                self.assertEqual(
                    validate(sample, scope_schema, self.schema) == [],
                    impact.valid_path(sample, allow_dot=True))

    def test_json_level_failures_are_fail_not_exceptions(self):
        bomb = b"[" * 200000
        valid = json.dumps(_base_minimal())
        cases = {
            "empty": (b"", "REPORT_NOT_JSON"),
            "not json": (b"this is not json", "REPORT_NOT_JSON"),
            "bom": (b"\xef\xbb\xbf" + valid.encode(), "REPORT_NOT_JSON"),
            "invalid utf-8": (valid.replace('"direct_fix"', '"\xff"').encode("latin-1"),
                              "REPORT_NOT_UTF8"),
            "duplicate keys in an otherwise valid report": (
                valid.replace('"mode": "direct_fix"', '"mode": "direct_fix", "mode": "direct_fix"')
                .encode(), "REPORT_NOT_JSON"),
            "NaN where a count belongs": (
                json.dumps(_base_minimal()).replace('"minimal"', '"minimal"').encode()
                .replace(b'"schema_version": 1', b'"schema_version": NaN'), "REPORT_SCHEMA_VERSION"),
            "top-level list": (b"[]", "REPORT_FIELD_TYPE"),
            "top-level string": (b'"report"', "REPORT_FIELD_TYPE"),
            "top-level null": (b"null", "REPORT_FIELD_TYPE"),
            "nesting bomb": (bomb, "REPORT_NOT_JSON"),
            "truncated": (valid.encode()[:-5], "REPORT_NOT_JSON"),
            "too large": (b" " * (impact.MAX_REPORT_BYTES + 1), "REPORT_TOO_LARGE"),
        }
        for name, (raw, expected) in cases.items():
            with self.subTest(name):
                parsed = impact.load_report(raw, "stdin", None)
                self.assertIsNone(parsed.data)
                self.assertIn(expected, codes(parsed.diagnostics, "fail"), name)
                self.assertEqual(parsed.sha256, hashlib.sha256(raw).hexdigest())
        # exactly at the limit is parsed, not rejected for size
        at_limit = valid.encode() + b" " * (impact.MAX_REPORT_BYTES - len(valid))
        self.assertEqual(len(at_limit), impact.MAX_REPORT_BYTES)
        self.assertIsNotNone(impact.load_report(at_limit, "stdin", None).data)

    def test_an_empty_evidence_list_is_reported_as_what_it_is(self):
        report = _base_minimal()
        report["evidence"] = []
        parsed = parse(report)
        self.assertIn(("REPORT_FIELD_TYPE", "evidence"),
                      [(d["code"], d["where"]) for d in parsed.diagnostics])

    def test_lone_surrogate_escape_in_a_string_does_not_crash(self):
        report = _base_minimal()
        report["evidence"][0]["summary"] = "x"
        raw = json.dumps(report).replace('"x"', '"\\ud800"').encode()
        parsed = impact.load_report(raw, "stdin", None)
        self.assertIsNotNone(parsed.data)  # structurally fine; rendering must still be safe

    def test_trailing_slash_names_a_directory_and_covers_only_its_children(self):
        self.assertTrue(impact.covers("src/", "src/a.py"))
        self.assertTrue(impact.covers("src", "src/a.py"))
        self.assertTrue(impact.covers("src/a.py", "src/a.py"))
        self.assertFalse(impact.covers("src", "src2/a.py"))
        self.assertFalse(impact.covers("src/a.py", "src/a.pyc"))
        self.assertFalse(impact.covers("src/a", "src/a.py"))
        self.assertTrue(impact.covers(".", "any/where.txt"))

    def test_many_errors_are_bounded(self):
        report = _base_full()
        report["categories"] = {name: 5 for name in CATEGORIES}
        report["evidence"] = [{"x": 1}] * 400
        parsed = impact.load_report(encode(report), "stdin", None)
        self.assertLessEqual(len(parsed.diagnostics), impact.MAX_DIAGNOSTICS + 1)
        self.assertIn("TOO_MANY_DIAGNOSTICS", codes(parsed.diagnostics, "fail"))

    def test_the_diagnostics_cap_keeps_the_most_severe_effect_it_hides(self):
        for hidden, expected in ((["note"], "note"), (["note", "skip"], "skip"),
                                 (["skip", "fail", "note"], "fail")):
            diags = impact._Diags()
            for index in range(impact.MAX_DIAGNOSTICS):
                diags.add("X", "note", "m")
            for effect in hidden:
                diags.add("Y", effect, "m")
            self.assertTrue(diags.has(expected))
            self.assertEqual(diags.finish()[-1]["effect"], expected)

    def test_oid_checks_reject_a_trailing_newline(self):
        for value in ("a" * 40 + "\n", "a" * 64 + "\n", "\n" + "a" * 40, "a" * 40 + " "):
            self.assertFalse(gitstate.is_oid(value), repr(value))
        self.assertFalse(gitstate.is_zero_oid("0" * 40 + "\n"))
        self.assertTrue(gitstate.is_oid("a" * 40) and gitstate.is_oid("b" * 64))
        self.assertTrue(gitstate.is_zero_oid("0" * 40) and gitstate.is_zero_oid("0" * 64))


# --- B. modes (B3, B3a, B3b) -----------------------------------------------------

class ModeTests(ImpactCase):
    """B3/B3a/B3b. Proven: each mode's required fields are present and consistent, the result
    names the mode, a SPEC that is new in the change contradicts a report saying none was
    created. Not proven (and not claimed): that /spec was not invoked, or that the plan exists
    and organised the work; the checker sees only what the report says."""

    def test_each_of_the_five_modes_passes_and_the_result_names_it(self):
        for mode in ALL_MODES:
            with self.subTest(mode):
                dim = self.dim(self.good_minimal(mode=mode))
                self.assertEqual((dim["status"], dim["mode"]), ("PASS", mode), dim["diagnostics"])

    def test_b3a_plan_driven_requires_the_plan_and_no_spec_and_no_spec_skill(self):
        dim = self.dim(self.good_minimal(mode="plan_driven"))
        self.assertEqual(dim["status"], "PASS")
        broken = self.good_minimal(mode="plan_driven")
        del broken["plan"]
        self.assertEqual(self.dim(broken)["status"], "FAIL")
        broken = self.good_minimal(mode="plan_driven")
        broken["process"]["spec_skill_invoked"] = True
        dim = self.dim(broken)
        self.assertEqual(dim["status"], "FAIL")
        self.assertIn("REPORT_MODE_CONTRADICTION", codes(dim["diagnostics"]))

    def test_b3b_audit_direct_and_follow_up_need_neither_plan_nor_spec(self):
        for mode in ("audit_test_driven", "direct_fix", "follow_up"):
            with self.subTest(mode):
                report = self.good_minimal(mode=mode)
                self.assertNotIn("plan", report)
                self.assertFalse(any(report["process"].values()))
                self.assertEqual(self.dim(report)["status"], "PASS")
                for key in ("spec_skill_invoked", "spec_created", "plan_created"):
                    contradicted = copy.deepcopy(report)
                    contradicted["process"][key] = True
                    self.assertEqual(self.dim(contradicted)["status"], "FAIL", key)

    def test_spec_driven_has_no_mode_specific_requirement_in_the_plan(self):
        report = self.good_minimal(mode="spec_driven")
        self.assertNotIn("process", report)
        self.assertEqual(self.dim(report)["status"], "PASS")

    def test_a_spec_new_in_the_change_contradicts_a_no_spec_report(self):
        D = self.commit_change({"SPEC.md": "# new spec\n"})
        report = minimal_report(self.tA, self.tree(D), ["src/a.py", "docs/a.md", "SPEC.md"],
                                mode="direct_fix", evidence=[review("e1", self.tree(D))])
        dim = self.dim(report)
        self.assertEqual(dim["status"], "FAIL")
        self.assertIn("SPEC_CREATED_CONTRADICTS_REPORT", codes(dim["diagnostics"], "fail"))
        for mode in ("plan_driven", "audit_test_driven", "follow_up"):
            with self.subTest(mode):
                other = copy.deepcopy(report)
                other.update(mode_fields(mode))
                other["mode"] = mode
                self.assertEqual(self.dim(other)["status"], "FAIL")

    def test_an_existing_plan_may_be_referenced_in_every_mode_but_contradictions_still_fail(self):
        for mode in ALL_MODES:
            with self.subTest(mode):
                report = self.good_minimal(mode=mode)
                report["plan"] = {"reference": "plans/earlier.md", "used": True}
                self.assertEqual(self.dim(report)["status"], "PASS")
                unused = copy.deepcopy(report)
                unused["plan"]["used"] = False
                self.assertEqual(self.dim(unused)["status"], "FAIL")
                if mode != "spec_driven":
                    invoked = copy.deepcopy(report)
                    invoked["process"]["spec_skill_invoked"] = True
                    self.assertEqual(self.dim(invoked)["status"], "FAIL")
        for mode in ("audit_test_driven", "direct_fix", "follow_up"):
            with self.subTest("created " + mode):
                report = self.good_minimal(mode=mode)
                report["plan"] = {"reference": "plans/earlier.md", "used": True}
                report["process"]["plan_created"] = True  # referencing is fine, creating is not
                self.assertEqual(self.dim(report)["status"], "FAIL")

    def test_a_follow_up_may_reuse_a_spec_and_a_plan_that_already_exist(self):
        self.commit_change({"SPEC.md": "# spec\n", "plans/earlier.md": "plan\n"}, "context")
        base = self.tree("HEAD")
        D = self.commit_change({"src/a.py": "a = 5\n"})
        report = minimal_report(base, self.tree(D), ["src/a.py"], mode="follow_up",
                                evidence=[review("e1", self.tree(D))])
        report["plan"] = {"reference": "plans/earlier.md", "used": True}
        dim = self.dim(report)
        self.assertEqual((dim["status"], dim["diagnostics"] and codes(dim["diagnostics"], "fail")),
                         ("PASS", []))

    def test_only_registered_spec_tracks_count_as_a_spec(self):
        # unregistered files that merely have the name are Stage 1's out-of-scope warning, not a SPEC
        for path in ("notes/Spec.md", "tests/fixtures/SPEC.md", "spec.md"):
            with self.subTest(path):
                D = self.commit_change({path: "# not a track\n"})
                report = minimal_report(self.tA, self.tree(D), self.changed_since_a(D),
                                        evidence=[review("e1", self.tree(D))])
                dim = self.dim(report)
                self.assertEqual(dim["status"], "PASS", dim["diagnostics"])
        policy = json.dumps({"schema_version": 1, "specs": [{"path": "docs/SPEC.md", "track": "d"}]})
        D = self.commit_change({"docs/SPEC.md": "# registered\n", ".docops-policy.json": policy})
        report = minimal_report(self.tA, self.tree(D), self.changed_since_a(D),
                                evidence=[review("e1", self.tree(D))])
        dim = self.dim(report)
        self.assertEqual(dim["status"], "FAIL")
        self.assertIn("SPEC_CREATED_CONTRADICTS_REPORT", codes(dim["diagnostics"], "fail"))

    def changed_since_a(self, commit):
        return self.fx.git("diff", "--no-renames", "--name-only", self.tA, commit).splitlines()

    def policy_for(self, path):
        return json.dumps({"schema_version": 1, "specs": [{"path": path, "track": "d"}]})

    def test_a_spec_made_from_a_note_is_creating_one_even_with_identical_content(self):
        self.commit_change({"docs/notes.md": "# the note\n"}, "note before the base")
        base = self.tree("HEAD")
        self.fx.git("mv", "docs/notes.md", "SPEC.md")  # same bytes, now the SPEC
        D = self.fx.commit("note becomes the SPEC")
        report = minimal_report(base, self.tree(D), ["docs/notes.md", "SPEC.md"],
                                evidence=[review("e1", self.tree(D))])
        dim = self.dim(report)
        self.assertEqual(dim["status"], "FAIL")
        self.assertIn("SPEC_CREATED_CONTRADICTS_REPORT", codes(dim["diagnostics"], "fail"))
        # the same change is fine for a SPEC-driven report
        report["mode"] = "spec_driven"
        report.pop("process")
        self.assertEqual(self.dim(report)["status"], "PASS")

    def test_registering_an_existing_note_as_a_spec_is_creating_one(self):
        base_commit = self.commit_change({"docs/notes.md": "# the note\n"}, "note before the base")
        base = self.tree("HEAD")
        for rewrite in (True, False):
            with self.subTest("rewritten" if rewrite else "registered as is"):
                self.fx.git("reset", "-q", "--hard", base_commit)  # each variant starts from the base
                files = {".docops-policy.json": self.policy_for("docs/notes.md")}
                if rewrite:
                    files["docs/notes.md"] = "# now a spec\n"
                D = self.commit_change(files, "register the note")
                report = minimal_report(base, self.tree(D), self.fx.git(
                    "diff", "--no-renames", "--name-only", base, D).splitlines(),
                    evidence=[review("e1", self.tree(D))])
                dim = self.dim(report)
                self.assertEqual(dim["status"], "FAIL")
                self.assertIn("SPEC_CREATED_CONTRADICTS_REPORT", codes(dim["diagnostics"], "fail"))
                silent = {**report, "mode": "spec_driven"}
                silent.pop("process")  # a mode 1 report that does not claim "no SPEC created"
                self.assertEqual(self.dim(silent, rev=D)["status"], "PASS")

    def test_a_registered_track_whose_file_was_absent_in_the_base_is_created_later(self):
        policy = {".docops-policy.json": self.policy_for("docs/SPEC.md")}
        self.commit_change(policy, "register a SPEC that does not exist yet")
        base = self.tree("HEAD")
        D = self.commit_change({"docs/SPEC.md": "# first version\n"})
        report = minimal_report(base, self.tree(D), ["docs/SPEC.md"],
                                evidence=[review("e1", self.tree(D))])
        dim = self.dim(report)
        self.assertEqual(dim["status"], "FAIL")
        self.assertIn("SPEC_CREATED_CONTRADICTS_REPORT", codes(dim["diagnostics"], "fail"))

    def test_a_relocated_spec_is_created_at_its_destination(self):
        # No correlation between paths is attempted: a SPEC that moves is created where it lands.
        for source, target in (("SPEC.md", "docs/SPEC.md"), ("docs/SPEC.md", "docs/SPEC2.md")):
            with self.subTest("%s -> %s" % (source, target)):
                files = {source: "# spec\n"}
                if source != "SPEC.md":
                    files[".docops-policy.json"] = self.policy_for(source)
                self.commit_change(files, "spec before the base")
                base = self.tree("HEAD")
                self.fx.git("mv", source, target)
                D = self.commit_change({".docops-policy.json": self.policy_for(target)}, "moved")
                names = self.fx.git("diff", "--no-renames", "--name-only", base, D).splitlines()
                report = minimal_report(base, self.tree(D), names,
                                        evidence=[review("e1", self.tree(D))])
                dim = self.dim(report)
                self.assertEqual(dim["status"], "FAIL")
                self.assertIn("SPEC_CREATED_CONTRADICTS_REPORT", codes(dim["diagnostics"], "fail"))
                self.fx.git("reset", "-q", "--hard", self.A)

    def test_a_relocation_is_acknowledged_by_saying_so_not_by_choosing_a_mode(self):
        self.commit_change({"SPEC.md": "# spec\n"}, "spec before the base")
        base = self.tree("HEAD")
        self.fx.git("mv", "SPEC.md", "docs/SPEC.md")
        D = self.commit_change({".docops-policy.json": self.policy_for("docs/SPEC.md")}, "moved")
        names = self.fx.git("diff", "--no-renames", "--name-only", base, D).splitlines()
        honest = minimal_report(base, self.tree(D), names, mode="direct_fix",
                                evidence=[review("e1", self.tree(D))])
        honest["process"]["spec_created"] = True
        self.assertEqual(self.dim(honest)["status"], "FAIL")  # modes 2-5 cannot say true
        for mode in ALL_MODES:
            with self.subTest(mode):
                claim = minimal_report(base, self.tree(D), names, mode=mode,
                                       evidence=[review("e1", self.tree(D))])
                claim.setdefault("process", {"spec_skill_invoked": False, "spec_created": False,
                                             "plan_created": False})
                dim = self.dim(claim)
                self.assertEqual(dim["status"], "FAIL", mode)  # spec_driven + spec_created false
                self.assertIn("SPEC_CREATED_CONTRADICTS_REPORT", codes(dim["diagnostics"], "fail"))
        acknowledged = minimal_report(base, self.tree(D), names, mode="spec_driven",
                                      evidence=[review("e1", self.tree(D))])
        acknowledged["process"] = {"spec_skill_invoked": False, "spec_created": True,
                                   "plan_created": False}
        self.assertEqual(self.dim(acknowledged)["status"], "PASS")
        silent = minimal_report(base, self.tree(D), names, mode="spec_driven",
                                evidence=[review("e1", self.tree(D))])
        self.assertEqual(self.dim(silent)["status"], "PASS")  # a mode 1 report may say nothing

    def test_removing_a_registration_while_its_file_appears_is_creation(self):
        # base policy registers docs/SPEC.md but no file exists; the change drops the registration
        # (an empty registry) and creates the file in the same change
        self.commit_change({".docops-policy.json": self.policy_for("docs/SPEC.md")}, "register")
        base = self.tree("HEAD")
        D = self.commit_change({".docops-policy.json": json.dumps({"schema_version": 1, "specs": []}),
                                "docs/SPEC.md": "# appears\n"}, "unregister and create")
        report = minimal_report(base, self.tree(D), [".docops-policy.json", "docs/SPEC.md"],
                                evidence=[review("e1", self.tree(D))])
        dim = self.dim(report)
        self.assertEqual(dim["status"], "FAIL")
        self.assertIn("SPEC_CREATED_CONTRADICTS_REPORT", codes(dim["diagnostics"], "fail"))
        code, result, _ = self.cli(report, "--tree", "HEAD")
        self.assertEqual(code, 1)

    def test_the_implicit_root_track_is_a_candidate_even_when_the_subject_registry_is_empty(self):
        base = self.tree("HEAD")  # no policy file: the implicit root SPEC.md track, no file
        D = self.commit_change({".docops-policy.json": json.dumps({"schema_version": 1, "specs": []}),
                                "SPEC.md": "# appears\n"}, "empty registry and a root SPEC")
        report = minimal_report(base, self.tree(D), ["SPEC.md", ".docops-policy.json"],
                                evidence=[review("e1", self.tree(D))])
        self.assertEqual(self.dim(report)["status"], "FAIL")

    def test_a_spec_that_stays_at_its_registered_path_is_not_created(self):
        self.commit_change({"docs/SPEC.md": "# spec\n",
                            ".docops-policy.json": self.policy_for("docs/SPEC.md")}, "registered")
        base = self.tree("HEAD")
        D = self.commit_change({"docs/SPEC.md": "# spec, edited\n"})
        report = minimal_report(base, self.tree(D), ["docs/SPEC.md"],
                                evidence=[review("e1", self.tree(D))])
        self.assertEqual(self.dim(report)["status"], "PASS")

    def test_an_unreadable_base_policy_stops_the_assessment_it_is_not_read_as_empty(self):
        # base policy is corrupt; the subject registers nothing and a root SPEC.md appears. Read as
        # an empty policy this would pass; it must not.
        for broken in ("{not json", json.dumps({"schema_version": 2, "specs": []}), ""):
            with self.subTest(broken[:15]):
                self.fx.git("reset", "-q", "--hard", self.B)
                self.commit_change({".docops-policy.json": broken}, "corrupt policy")
                base = self.tree("HEAD")
                D = self.commit_change({".docops-policy.json": json.dumps(
                    {"schema_version": 1, "specs": []}), "SPEC.md": "# appears\n"}, "fix and create")
                report = minimal_report(base, self.tree(D), ["SPEC.md", ".docops-policy.json"],
                                        evidence=[review("e1", self.tree(D))])
                code, result, _ = self.cli(report, "--tree", "HEAD")
                snap = result["snapshots"][0]
                dim = snap["checks"]["DOC_IMPACT_ASSESSMENT"]
                self.assertEqual((dim["status"], snap["status"], code), ("SKIP", "SKIP", 1))
                self.assertEqual(codes(dim["diagnostics"], "skip"), ["SPEC_POLICY_UNREADABLE"])
                self.assertEqual(dim["portions"], [])
                self.assertEqual(result["summary"]["tool_errors"], 0)
                self.assertValidResult(result)

    def test_an_unreadable_base_policy_does_not_matter_when_no_claim_needs_it(self):
        self.commit_change({".docops-policy.json": "{not json"}, "corrupt policy")
        base = self.tree("HEAD")
        D = self.commit_change({".docops-policy.json": json.dumps({"schema_version": 1, "specs": []}),
                                "SPEC.md": "# appears\n"})
        report = minimal_report(base, self.tree(D), ["SPEC.md", ".docops-policy.json"],
                                mode="spec_driven", evidence=[review("e1", self.tree(D))])
        self.assertEqual(self.dim(report)["status"], "PASS")

    def test_the_two_policies_fail_differently_by_role(self):
        # ADR-0014 Decision 2: an invalid SUBJECT policy is Stage 1's tool error (exit 2,
        # POLICY_INVALID) before any impact assessment; a Git failure while reading the BASE
        # policy is IMPACT_GIT_ERROR (exit 2); only an unreadable BASE policy is SKIP / exit 1.
        base = self.tree("HEAD")
        D = self.commit_change({".docops-policy.json": "{not json"}, "corrupt subject policy")
        report = minimal_report(base, self.tree(D), [".docops-policy.json"],
                                evidence=[review("e1", self.tree(D))])
        code, result, _ = self.cli(report, "--tree", "HEAD")
        self.assertEqual(code, 2)
        self.assertToolError(result["snapshots"][0], "POLICY_INVALID")
        self.fx.git("reset", "-q", "--hard", self.B)
        D = self.commit_change({"src/a.py": "x = 2\n"}, "valid subject policy")
        report = minimal_report(base, self.tree(D), ["src/a.py"],
                                evidence=[review("e1", self.tree(D))])
        repo = gitstate.open_repository(self.fx.root)
        real = checks._load_policy
        calls = []

        def fail_on_the_second_read(snapshot, by_path):
            calls.append(snapshot.kind)
            if len(calls) == 2:  # the first read is the subject's own SPEC check
                raise GitError("boom")
            return real(snapshot, by_path)

        with mock.patch.object(checks, "_load_policy", side_effect=fail_on_the_second_read):
            result = checks.check_request(repo, {"kind": "rev", "rev": "HEAD"}, parse(report))
        self.assertEqual(len(calls), 2)
        self.assertToolError(result, "IMPACT_GIT_ERROR")

    def test_a_spec_driven_change_may_add_a_spec(self):
        D = self.commit_change({"SPEC.md": "# new spec\n"})
        report = minimal_report(self.tA, self.tree(D), ["src/a.py", "docs/a.md", "SPEC.md"],
                                mode="spec_driven", evidence=[review("e1", self.tree(D))])
        self.assertEqual(self.dim(report)["status"], "PASS")

    def test_editing_an_existing_spec_is_not_creating_one(self):
        self.commit_change({"SPEC.md": "# spec\n"}, "add spec before the base")
        base_tree = self.tree("HEAD")
        D = self.commit_change({"SPEC.md": "# spec v2\n"})
        report = minimal_report(base_tree, self.tree(D), ["SPEC.md"],
                                evidence=[review("e1", self.tree(D))])
        self.assertEqual(self.dim(report)["status"], "PASS")


# --- C. profiles (B4) ----------------------------------------------------------------

class ProfileTests(ImpactCase):
    """B4. Proven: a minimal report and a full report each need exactly their own fields; the
    checker does not decide which profile a change deserves (a large change reported as minimal
    passes if it is complete) and does not decide whether a stated reason is true."""

    def test_valid_minimal_report_passes(self):
        dim = self.dim(self.good_minimal())
        self.assertEqual((dim["status"], dim["profile"]), ("PASS", "minimal"))
        self.assertEqual([p["name"] for p in dim["portions"]], ["minimal"])

    def test_valid_full_report_passes_with_all_eight_categories(self):
        report = full_report(self.tA, self.tB, {"architecture": ["src/a.py"],
                                                "interfaces": ["docs/a.md"]})
        dim = self.dim(report)
        self.assertEqual((dim["status"], dim["profile"]), ("PASS", "full"), dim["diagnostics"])
        self.assertEqual([p["name"] for p in dim["portions"]], list(CATEGORIES))
        self.assertTrue(all(p["status"] == "PASS" for p in dim["portions"]))

    def test_a_full_report_with_a_missing_category_fails(self):
        report = full_report(self.tA, self.tB, {"architecture": ["src/a.py", "docs/a.md"]})
        del report["categories"]["decisions"]
        dim = self.dim(report)
        self.assertEqual(dim["status"], "FAIL")
        self.assertIn("REPORT_CATEGORY_MISSING", codes(dim["diagnostics"]))

    def test_the_profile_is_not_judged_from_the_size_of_the_change(self):
        files = {"big/f%02d.txt" % i: "x%d\n" % i for i in range(30)}
        D = self.commit_change(files)
        paths = ["src/a.py", "docs/a.md", "big/"]
        report = minimal_report(self.tA, self.tree(D), paths, evidence=[review("e1", self.tree(D))],
                                reason="trivial, honest")
        dim = self.dim(report)
        self.assertEqual(dim["status"], "PASS")
        self.assertEqual(dim["base"]["changed_paths"], 32)

    def test_a_minimal_report_that_omits_a_changed_path_is_incomplete(self):
        report = minimal_report(self.tA, self.tB, ["src/a.py"])
        dim = self.dim(report)
        self.assertEqual(dim["status"], "SKIP")
        self.assertEqual(codes(dim["diagnostics"], "skip"), ["CHANGED_PATH_NOT_REPORTED"])
        gap = [d for d in dim["diagnostics"] if d["code"] == "CHANGED_PATH_NOT_REPORTED"]
        self.assertIn("docs/a.md", gap[0]["message"])

    def test_a_full_report_that_names_no_path_of_the_change_is_incomplete(self):
        dim = self.dim(full_report(self.tA, self.tB))
        self.assertEqual(dim["status"], "SKIP")
        self.assertIn("CHANGED_PATH_NOT_REPORTED", codes(dim["diagnostics"]))

    def test_affected_paths_are_the_union_over_categories(self):
        report = full_report(self.tA, self.tB, {"architecture": ["src/"], "tests": ["docs/a.md"]})
        self.assertEqual(self.dim(report)["status"], "PASS")

    def test_a_no_impact_claim_needs_evidence_over_the_whole_change(self):
        narrow = review("e1", self.tB, scope=("docs/",))
        report = full_report(self.tA, self.tB, {"architecture": ["docs/a.md", "src/a.py"]},
                             evidence=[narrow, review("e2", self.tB, scope=("src/", "docs/"))],
                             cited=["e2"])
        report["categories"]["tests"] = {"no_impact_reason": "no test depends on it",
                                         "evidence": ["e1"]}
        dim = self.dim(report)
        self.assertEqual(dim["status"], "SKIP")
        self.assertEqual(codes(dim["diagnostics"], "skip"), ["EVIDENCE_SCOPE_GAP"])
        gaps = [p["name"] for p in dim["portions"] if p["status"] != "PASS"]
        self.assertEqual(gaps, ["tests"])

    def test_evidence_must_cover_the_paths_a_portion_claims(self):
        report = minimal_report(self.tA, self.tB, ["src/a.py", "docs/a.md"],
                                evidence=[review("e1", self.tB, scope=("src/",))])
        dim = self.dim(report)
        self.assertEqual((dim["status"], codes(dim["diagnostics"], "skip")),
                         ("SKIP", ["EVIDENCE_SCOPE_GAP"]))

    def test_a_complete_but_false_report_passes_and_says_it_proves_nothing_semantic(self):
        # The report claims nothing is affected by a change that rewrote the only source file.
        # Structure, binding, completeness and freshness hold, so it passes; whether the
        # claims are true is exactly what the validator does not decide.
        false_claims = full_report(self.tA, self.tB, {"tests": ["src/a.py", "docs/a.md"]})
        for entry in false_claims["categories"].values():
            if "no_impact_reason" in entry:
                entry["no_impact_reason"] = "this change cannot affect anything, trust me"
        result = self.assess(false_claims)
        dim = result["checks"]["DOC_IMPACT_ASSESSMENT"]
        self.assertEqual(dim["status"], "PASS")
        self.assertEqual(dim["semantic_review"], "not established")
        self.assertEqual(dim["validated"], ["structure", "identity", "completeness", "freshness"])
        self.assertIn("whether its statements are true is not", dim["reason"])
        self.assertIn("completeness relative to the reported base", dim["reason"])
        for other in ("TESTS", "DOC_CONSISTENCY_REVIEW", "ACCEPTANCE_VERIFICATION",
                      "PUBLICATION", "OWNER_ACCEPTANCE"):
            self.assertEqual(set(result["checks"][other]), {"assessed", "reason"}, other)
            self.assertIs(result["checks"][other]["assessed"], False)


# --- D. evidence freshness (B5) -----------------------------------------------------------

class FreshnessTests(ImpactCase):
    """B5. Proven: evidence names the tree it was gathered on and the paths it covers; when a
    path in that scope differs between its tree and the subject the evidence is stale and its
    portion needs renewed review, while evidence whose scope did not change stays valid and its
    portions stay assessed; the overall result is never PASS while any required portion is
    stale. Not proven: that unrelated paths outside every evidence scope cannot matter."""

    def setUp(self):
        super().setUp()
        self.C = self.commit_change({"src/a.py": "a = 3\n"}, "C: src only")
        self.tC = self.tree(self.C)

    def two_scope_report(self, src_tree):
        evidence = [review("e_src", src_tree, scope=("src/",)),
                    review("e_docs", self.tB, scope=("docs/",)),
                    review("e_all", self.tC, scope=("."))]
        report = full_report(self.tA, self.tC, evidence=evidence, cited=["e_all"],
                             affected={"architecture": ["src/a.py"], "interfaces": ["docs/a.md"]})
        report["categories"]["architecture"]["evidence"] = ["e_src"]
        report["categories"]["interfaces"]["evidence"] = ["e_docs"]
        return report

    def portions(self, dim):
        return {p["name"]: p for p in dim["portions"]}

    def test_only_the_affected_portion_goes_stale_and_the_rest_stays_assessed(self):
        dim = self.dim(self.two_scope_report(self.tB))
        by_name = self.portions(dim)
        self.assertEqual(by_name["architecture"]["status"], "SKIP")
        self.assertEqual(by_name["architecture"]["evidence"][0]["freshness"], "stale")
        self.assertIn("src/a.py", by_name["architecture"]["evidence"][0]["reason"])
        self.assertEqual(by_name["interfaces"]["status"], "PASS")
        self.assertEqual(by_name["interfaces"]["evidence"][0]["freshness"], "fresh")
        others = [p for n, p in by_name.items() if n not in ("architecture",)]
        self.assertTrue(all(p["status"] == "PASS" for p in others))
        self.assertEqual(dim["status"], "SKIP")
        self.assertIn("renewed review", dim["reason"])

    def test_the_overall_result_is_not_a_pass_while_a_required_portion_is_stale(self):
        code, result, _ = self.cli(self.two_scope_report(self.tB), "--tree", "HEAD")
        self.assertEqual(code, 1)  # a required assessment is incomplete: never 0, never a tool error
        self.assertEqual(result["snapshots"][0]["status"], "SKIP")  # the per-check status is kept
        self.assertEqual(result["summary"]["status"], "SKIP")
        self.assertEqual((result["snapshots"][0]["exit_code"], result["summary"]["exit_code"]), (1, 1))
        self.assertValidResult(result)

    def test_renewed_evidence_on_the_new_tree_restores_the_pass(self):
        code, result, _ = self.cli(self.two_scope_report(self.tC), "--tree", "HEAD")
        self.assertEqual((code, result["snapshots"][0]["status"]), (0, "PASS"))
        dim = result["snapshots"][0]["checks"]["DOC_IMPACT_ASSESSMENT"]
        self.assertTrue(all(p["status"] == "PASS" for p in dim["portions"]))

    def test_a_report_for_an_earlier_subject_is_not_valid_for_a_later_one(self):
        report = self.good_minimal(subject=self.tB)
        self.assertEqual(self.dim(report, rev=self.B)["status"], "PASS")
        later = self.dim(report, rev=self.C)
        self.assertEqual(later["status"], "FAIL")
        self.assertEqual(codes(later["diagnostics"], "fail"), ["SUBJECT_MISMATCH"])

    def stale_after(self, mutate, scope, expect, old_tree=None):
        """Evidence gathered on `old_tree` (default B) over `scope`; `mutate` then makes a
        commit D on top of C."""
        old_tree = old_tree or self.tB
        mutate()
        tD = self.tree("HEAD")
        changed = self.fx.git("diff", "--no-renames", "--name-only", self.tA, tD).splitlines()
        report = minimal_report(self.tA, tD, changed, evidence=[
            review("e_all", tD, scope=(".",)), review("e_old", old_tree, scope=scope)])
        dim = self.dim(report)
        self.assertEqual(dim["portions"][0]["evidence"][1]["freshness"], expect,
                         dim["portions"][0]["evidence"])
        return dim

    def test_change_inside_the_scope_is_stale(self):
        dim = self.stale_after(lambda: self.commit_change({"docs/a.md": "v3\n"}), ("docs/",), "stale")
        self.assertEqual(dim["status"], "SKIP")

    def test_change_outside_the_scope_is_fresh(self):
        dim = self.stale_after(lambda: self.commit_change({"README.md": "r2\n"}), ("docs/",), "fresh")
        self.assertEqual(dim["status"], "PASS")

    def test_a_sibling_directory_sharing_a_name_prefix_is_not_in_scope(self):
        self.stale_after(lambda: self.commit_change({"docs2/x.md": "x2\n"}), ("docs",), "fresh")

    def test_a_file_scope_is_exact(self):
        self.stale_after(lambda: self.commit_change({"src/a.pyc": "x\n"}), ("src/a.py",), "fresh",
                         old_tree=self.tC)

    def test_whole_tree_scope_goes_stale_on_any_change(self):
        self.stale_after(lambda: self.commit_change({"README.md": "r2\n"}), (".",), "stale")

    def test_a_deleted_file_in_scope_is_stale(self):
        self.stale_after(lambda: self.commit_change({}, remove=["docs/a.md"]), ("docs/",), "stale")

    def test_an_added_file_in_scope_is_stale(self):
        self.stale_after(lambda: self.commit_change({"docs/new.md": "n\n"}), ("docs/",), "stale")

    def test_a_mode_change_in_scope_is_stale(self):
        def chmod():
            self.fx.git("update-index", "--chmod=+x", "src/a.py")
            self.fx.commit("chmod")
        self.stale_after(chmod, ("src/",), "stale", old_tree=self.tC)

    def test_evidence_gathered_on_the_subject_tree_is_fresh_whatever_its_scope(self):
        dim = self.dim(minimal_report(self.tA, self.tC, ["src/a.py", "docs/a.md"],
                                      evidence=[review("e1", self.tC, scope=("nowhere/",)),
                                                review("e2", self.tC, scope=("."))]))
        self.assertEqual([e["freshness"] for e in dim["portions"][0]["evidence"]],
                         ["fresh", "fresh"])

    def test_evidence_on_a_tree_missing_from_the_repository_is_unverifiable_not_fresh(self):
        report = minimal_report(self.tA, self.tC, ["src/a.py", "docs/a.md"], evidence=[
            review("e1", self.tC), review("e2", "9" * 40)])
        dim = self.dim(report)
        self.assertEqual(dim["status"], "SKIP")
        self.assertEqual(dim["portions"][0]["evidence"][1]["freshness"], "unverifiable")
        self.assertIn("EVIDENCE_TREE_NOT_AVAILABLE", codes(dim["diagnostics"], "skip"))

    def test_evidence_cited_by_no_portion_is_noted_and_does_not_affect_the_result(self):
        report = minimal_report(self.tA, self.tC, ["src/a.py", "docs/a.md"], cited=["e1"],
                                evidence=[review("e1", self.tC), review("orphan", "9" * 40),
                                          review("orphan2", self.B)])
        dim = self.dim(report)
        self.assertEqual(dim["status"], "PASS")
        self.assertEqual(codes(dim["diagnostics"], "note"),
                         ["BASE_NOT_INDEPENDENTLY_VERIFIED", "EVIDENCE_NOT_CITED"])
        self.assertEqual([e["id"] for e in dim["portions"][0]["evidence"]], ["e1"])

    def test_a_current_failure_cannot_be_hidden_by_citing_it_nowhere(self):
        failing = tests_evidence("t1", self.tC, outcome="fail", run=5, passed=4, failed=1,
                                 exit_code=1)
        report = minimal_report(self.tA, self.tC, ["src/a.py", "docs/a.md"], cited=["e1"],
                                evidence=[review("e1", self.tC), failing])
        dim = self.dim(report)
        self.assertEqual(dim["status"], "FAIL")
        self.assertIn("UNCITED_EVIDENCE_RECORDS_FAILURE", codes(dim["diagnostics"], "fail"))
        self.assertEqual(dim["portions"][0]["status"], "PASS")

    def uncited_failure(self, tree, scope):
        failing = tests_evidence("t1", tree, scope=scope, outcome="fail", run=5, passed=4,
                                 failed=1, exit_code=1)
        report = minimal_report(self.tA, self.tC, ["src/a.py", "docs/a.md"], cited=["e1"],
                                evidence=[review("e1", self.tC), failing])
        return self.dim(report)

    def test_an_uncited_failure_on_a_stale_tree_is_only_a_note(self):
        dim = self.uncited_failure(self.tB, ("src/",))
        self.assertEqual(dim["status"], "PASS")
        self.assertEqual(codes(dim["diagnostics"], "fail") + codes(dim["diagnostics"], "skip"), [])
        self.assertIn("EVIDENCE_NOT_CITED", codes(dim["diagnostics"], "note"))

    def test_an_uncited_failure_on_an_absent_tree_makes_the_result_a_skip(self):
        dim = self.uncited_failure("9" * 40, (".",))
        self.assertEqual(dim["status"], "SKIP")
        self.assertEqual(codes(dim["diagnostics"], "skip"), ["EVIDENCE_TREE_NOT_AVAILABLE"])
        self.assertEqual(codes(dim["diagnostics"], "fail"), [])

    def test_many_unverifiable_evidence_entries_stay_a_skip_not_a_fail(self):
        many = [review("x%03d" % i, "9" * 40) for i in range(impact.MAX_DIAGNOSTICS + 20)]
        report = minimal_report(self.tA, self.tC, ["src/a.py", "docs/a.md"],
                                evidence=[review("e1", self.tC), *many])
        dim = self.dim(report)
        self.assertEqual(dim["status"], "SKIP")
        self.assertEqual(codes(dim["diagnostics"], "fail"), [])
        self.assertEqual(dim["diagnostics"][-1]["code"], "TOO_MANY_DIAGNOSTICS")
        self.assertEqual(dim["diagnostics"][-1]["effect"], "skip")

    def test_evidence_tree_that_is_a_commit_is_rejected(self):
        report = minimal_report(self.tA, self.tC, ["src/a.py", "docs/a.md"], evidence=[
            review("e1", self.tC), review("e2", self.B)])
        dim = self.dim(report)
        self.assertEqual(dim["status"], "FAIL")
        self.assertEqual(dim["portions"][0]["evidence"][1]["freshness"], "invalid")
        self.assertEqual(dim["portions"][0]["status"], "FAIL")

    def test_stale_evidence_of_a_failed_test_is_stale_not_a_recorded_failure(self):
        failing = tests_evidence("t1", self.tB, scope=("src/",), outcome="fail", run=5, passed=4,
                                failed=1, exit_code=1)
        report = minimal_report(self.tA, self.tC, ["src/a.py", "docs/a.md"],
                                evidence=[review("e1", self.tC), failing])
        dim = self.dim(report)
        self.assertEqual((dim["status"], dim["portions"][0]["evidence"][1]["freshness"]),
                         ("SKIP", "stale"))

    def test_fresh_evidence_recording_a_failure_fails_the_portion(self):
        failing = tests_evidence("t1", self.tC, outcome="fail", run=5, passed=4, failed=1, exit_code=1)
        report = minimal_report(self.tA, self.tC, ["src/a.py", "docs/a.md"],
                                evidence=[review("e1", self.tC), failing])
        code, result, _ = self.cli(report, "--tree", "HEAD")
        self.assertEqual(code, 1)
        dim = result["snapshots"][0]["checks"]["DOC_IMPACT_ASSESSMENT"]
        self.assertEqual((dim["status"], dim["portions"][0]["status"]), ("FAIL", "FAIL"))

    def test_a_failure_is_not_downgraded_by_other_incomplete_evidence_in_the_same_portion(self):
        failing = tests_evidence("t1", self.tC, outcome="fail", run=5, passed=4, failed=1,
                                 exit_code=1)
        partial = tests_evidence("t2", self.tC, outcome="partial", run=5, passed=5, exit_code=None)
        for order in ((failing, partial), (partial, failing)):
            report = minimal_report(self.tA, self.tC, ["src/a.py", "docs/a.md"],
                                    evidence=[review("e1", self.tC), *order])
            dim = self.dim(report)
            self.assertEqual((dim["status"], dim["portions"][0]["status"]), ("FAIL", "FAIL"))

    def test_partial_and_skipped_evidence_cannot_pass(self):
        for outcome in ("partial", "skipped"):
            with self.subTest(outcome):
                item = tests_evidence("t1", self.tC, outcome=outcome, run=5, passed=3, skipped=2,
                                     exit_code=None) if outcome == "skipped" else \
                    tests_evidence("t1", self.tC, outcome=outcome, run=5, passed=5, exit_code=None)
                report = minimal_report(self.tA, self.tC, ["src/a.py", "docs/a.md"],
                                        evidence=[review("e1", self.tC), item])
                dim = self.dim(report)
                self.assertEqual(dim["status"], "SKIP")
                self.assertEqual(dim["portions"][0]["status"], "SKIP")

    def test_missing_tests_cannot_pass(self):
        item = tests_evidence("t1", self.tC, run=0, passed=0)
        dim = self.dim(minimal_report(self.tA, self.tC, ["src/a.py", "docs/a.md"],
                                      evidence=[review("e1", self.tC), item]))
        self.assertEqual(dim["status"], "FAIL")
        self.assertIn("REPORT_EVIDENCE_INCONSISTENT", codes(dim["diagnostics"]))

    def test_a_commit_message_or_a_bare_exit_code_does_not_close_anything(self):
        self.commit_change({"src/a.py": "a = 4\n"}, "fix everything\n\nCloses: B-001\n")
        tD = self.tree("HEAD")
        for kind in ("commit", "commit_message", "trailer", "exit_code"):
            with self.subTest(kind):
                item = review("e1", tD)
                item["kind"] = kind
                dim = self.dim(minimal_report(self.tA, tD, ["src/a.py", "docs/a.md"],
                                              evidence=[item]))
                self.assertEqual(dim["status"], "FAIL")
                self.assertIn("REPORT_EVIDENCE_KIND", codes(dim["diagnostics"]))
        bare = tests_evidence("t1", tD)
        del bare["tests"]
        dim = self.dim(minimal_report(self.tA, tD, ["src/a.py", "docs/a.md"],
                                      evidence=[review("e1", tD), bare]))
        self.assertEqual(dim["status"], "FAIL")
        self.assertIn("REPORT_FIELD_MISSING", codes(dim["diagnostics"]))
        closes = minimal_report(self.tA, tD, ["src/a.py", "docs/a.md"])
        closes["closes"] = "B-001"
        self.assertEqual(self.dim(closes)["status"], "FAIL")


# --- E. identity ----------------------------------------------------------------------------

class IdentityTests(ImpactCase):
    def test_subject_must_be_the_snapshot_under_check(self):
        report = self.good_minimal()
        self.assertEqual(self.dim(report, rev=self.B)["status"], "PASS")
        dim = self.dim(report, rev=self.A)
        self.assertEqual((dim["status"], dim["subject"]["matches"]), ("FAIL", False))
        self.assertEqual(dim["subject"]["snapshot_tree"], self.tA)
        self.assertEqual(codes(dim["diagnostics"], "fail"), ["SUBJECT_MISMATCH"])
        self.assertEqual(dim["portions"], [])  # nothing else is judged against the wrong snapshot
        self.assertIsNone(dim["base"]["changed_paths"])

    def test_a_good_report_for_one_snapshot_cannot_rescue_another_in_the_same_run(self):
        report = encode(self.good_minimal())
        code, result, _ = run_check_json(["--repo", self.fx.root, "--tree", self.B, "--tree", self.A,
                                          "--impact-file", "-"], stdin=report)
        statuses = [s["checks"]["DOC_IMPACT_ASSESSMENT"]["status"] for s in result["snapshots"]]
        self.assertEqual(statuses, ["PASS", "FAIL"])
        self.assertEqual((code, result["summary"]["status"]), (1, "FAIL"))

    def test_index_snapshot_is_bound_by_its_computed_tree_not_the_worktree(self):
        self.fx.stage("src/a.py", "a = 9\n")
        staged_tree = self.fx.git("write-tree")
        report = minimal_report(self.tA, staged_tree, ["src/a.py", "docs/a.md"],
                                evidence=[review("e1", staged_tree)])
        self.fx.write("src/a.py", "a = unstaged edit\n")  # invisible to the index
        self.assertEqual(self.dim(report, rev="index")["status"], "PASS")
        stale = minimal_report(self.tA, self.tB, ["src/a.py", "docs/a.md"])
        dim = self.dim(stale, rev="index")
        self.assertEqual((dim["status"], dim["subject"]["snapshot_tree"]), ("FAIL", staged_tree))

    def test_the_index_tree_need_not_exist_as_an_object_to_bind_a_report(self):
        self.fx.stage("src/a.py", "a = 10\n")
        repo = gitstate.open_repository(self.fx.root)
        staged_tree = gitstate.index_tree_oid(gitstate.index_snapshot(repo).entries, 40)
        absent = subprocess.run(["git", "-C", self.fx.root, "cat-file", "-e", staged_tree],
                                capture_output=True, env=clean_env())
        self.assertNotEqual(absent.returncode, 0, "the tree was written; the test is void")
        report = minimal_report(self.tA, staged_tree, ["src/a.py", "docs/a.md"],
                                evidence=[review("e1", staged_tree)])
        dim = self.dim(report, rev="index")
        self.assertEqual((dim["status"], dim["subject"]["matches"]), ("PASS", True))
        self.assertEqual(dim["portions"][0]["evidence"][0]["freshness"], "fresh")
        after = subprocess.run(["git", "-C", self.fx.root, "cat-file", "-e", staged_tree],
                               capture_output=True, env=clean_env())
        self.assertNotEqual(after.returncode, 0, "validation wrote the index tree")

    def test_intent_to_add_entries_fail_closed(self):
        self.fx.write("new.txt", "n\n")
        self.fx.git("add", "-N", "new.txt")
        written = self.fx.git("write-tree")  # Git leaves intent-to-add entries out
        report = minimal_report(self.tA, written, ["src/a.py", "docs/a.md"],
                                evidence=[review("e1", written)])
        dim = self.dim(report, rev="index")
        self.assertEqual((dim["status"], codes(dim["diagnostics"], "fail")),
                         ("FAIL", ["SUBJECT_MISMATCH"]))

    def test_unmerged_index_has_no_tree_and_cannot_pass(self):
        blob = self.fx.git("hash-object", "-w", "--stdin", input_bytes=b"x\n")
        info = "".join("100644 %s %d\tconflict.txt\n" % (blob, stage) for stage in (1, 2, 3))
        self.fx.git("update-index", "--index-info", input_bytes=info.encode())
        dim = self.dim(self.good_minimal(), rev="index")
        self.assertEqual(dim["status"], "FAIL")
        self.assertIn("SUBJECT_TREE_UNAVAILABLE", codes(dim["diagnostics"], "fail"))

    def test_base_that_is_absent_cannot_be_compared_and_is_not_a_pass(self):
        report = self.good_minimal()
        report["base_tree"] = "1" * 40
        dim = self.dim(report)
        self.assertEqual((dim["status"], codes(dim["diagnostics"], "skip")),
                         ("SKIP", ["BASE_NOT_AVAILABLE"]))
        self.assertIsNone(dim["base"]["changed_paths"])

    def test_base_naming_a_commit_or_blob_is_rejected(self):
        blob = self.fx.git("rev-parse", "HEAD:README.md")
        for oid in (self.A, blob):
            with self.subTest(oid):
                report = self.good_minimal()
                report["base_tree"] = oid
                dim = self.dim(report)
                self.assertEqual((dim["status"], codes(dim["diagnostics"], "fail")),
                                 ("FAIL", ["BASE_NOT_A_TREE"]))

    def test_a_report_whose_base_equals_its_subject_cannot_describe_a_change(self):
        report = minimal_report(self.tB, self.tB, ["src/a.py"])
        dim = self.dim(report)
        self.assertEqual((dim["status"], codes(dim["diagnostics"], "fail")),
                         ("FAIL", ["REPORT_BASE_EQUALS_SUBJECT"]))
        self.assertEqual(dim["portions"], [])
        code, result, _ = self.cli(report, "--tree", "HEAD")
        self.assertEqual((code, result["snapshots"][0]["status"]), (1, "FAIL"))

    def test_a_real_no_op_commit_is_not_a_change_either(self):
        self.fx.commit("same tree, new commit")
        self.assertEqual(self.tree("HEAD"), self.tB)
        code, result, _ = self.cli(minimal_report(self.tB, self.tree("HEAD"), ["src/a.py"]),
                                   "--tree", "HEAD")
        self.assertEqual((code, result["snapshots"][0]["status"]), (1, "FAIL"))

    def test_an_empty_comparison_with_an_unverifiable_evidence_tree_never_exits_zero(self):
        report = minimal_report(self.tB, self.tB, ["src/a.py"],
                                evidence=[review("e1", self.tB), review("e2", "9" * 40)])
        code, result, _ = self.cli(report, "--tree", "HEAD")
        dim = result["snapshots"][0]["checks"]["DOC_IMPACT_ASSESSMENT"]
        self.assertEqual((code, dim["status"], result["summary"]["status"]), (1, "FAIL", "FAIL"))
        self.assertIn("REPORT_BASE_EQUALS_SUBJECT", codes(dim["diagnostics"], "fail"))

    def test_without_a_supplied_base_the_base_is_recorded_but_not_called_verified(self):
        dim = self.dim(self.good_minimal())
        self.assertFalse(dim["base"]["independently_verified"])
        self.assertEqual(dim["base"]["verification"], "none")
        self.assertIn("BASE_NOT_INDEPENDENTLY_VERIFIED", codes(dim["diagnostics"], "note"))
        self.assertIn("the base was not independently verified", dim["reason"])

    def test_push_old_tip_verifies_the_base(self):
        dim = self.dim(self.good_minimal(), rev=self.B, remote_oid=self.A)
        self.assertEqual(dim["status"], "PASS")
        self.assertTrue(dim["base"]["independently_verified"])
        self.assertEqual(dim["base"]["verification"], "push-remote-tip")
        self.assertNotIn("not independently verified", dim["reason"])

    def test_push_old_tip_with_a_different_tree_than_the_reported_base_fails(self):
        report = self.good_minimal()  # base tA, subject tB
        dim = self.dim(report, rev=self.B, remote_oid=self.B)  # old tip B has tree tB, not tA
        self.assertEqual((dim["status"], codes(dim["diagnostics"], "fail")),
                         ("FAIL", ["BASE_MISMATCH"]))
        self.assertFalse(dim["base"]["independently_verified"])

    def test_push_old_tip_unknown_locally_is_not_verifiable(self):
        dim = self.dim(self.good_minimal(), rev=self.B, remote_oid="7" * 40)
        self.assertEqual((dim["status"], codes(dim["diagnostics"], "skip")),
                         ("SKIP", ["BASE_NOT_VERIFIABLE"]))
        self.assertFalse(dim["base"]["independently_verified"])

    def test_push_old_tip_that_is_not_an_object_id_is_not_verifiable(self):
        for tip in ("abc", "HEAD", "refs/heads/main", "x" * 40, self.tB):
            with self.subTest(tip):
                dim = self.dim(self.good_minimal(), rev=self.B, remote_oid=tip)
                self.assertEqual((dim["status"], dim["base"]["independently_verified"]),
                                 ("SKIP", False))
                self.assertEqual(codes(dim["diagnostics"], "skip"), ["BASE_NOT_VERIFIABLE"])

    def test_push_of_a_new_ref_supplies_no_base(self):
        dim = self.dim(self.good_minimal(), rev=self.B, remote_oid=ZERO)
        self.assertEqual(dim["status"], "PASS")
        self.assertFalse(dim["base"]["independently_verified"])
        self.assertEqual(dim["base"]["verification"], "none")

    def push_only_deletion(self, raw):
        line = "(delete) %s refs/heads/gone %s\n" % (ZERO, self.A)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "report.json")
            with open(path, "wb") as handle:
                handle.write(raw)
            return run_check_json(["--repo", self.fx.root, "--push-stdin", "--impact-file", path],
                                  stdin=line.encode())

    def test_a_malformed_report_fails_even_when_the_only_push_is_a_deletion(self):
        for raw in (b"{}", b"not json", b"", encode(self.good_minimal())[:-3]):
            with self.subTest(raw[:12]):
                code, result, _ = self.push_only_deletion(raw)
                snap = result["snapshots"][0]
                dim = snap["checks"]["DOC_IMPACT_ASSESSMENT"]
                self.assertEqual((dim["status"], snap["status"], code), ("FAIL", "FAIL", 1))
                self.assertEqual(result["summary"]["status"], "FAIL")
                self.assertValidResult(result)

    def test_a_valid_report_with_only_a_deletion_is_not_assessed_and_is_not_a_failure(self):
        code, result, _ = self.push_only_deletion(encode(self.good_minimal()))
        snap = result["snapshots"][0]
        self.assertEqual((snap["checks"]["DOC_IMPACT_ASSESSMENT"]["assessed"], snap["status"], code),
                         (False, "N/A", 0))

    def test_push_stdin_judges_each_ref_separately_with_one_report(self):
        report = self.good_minimal()
        lines = ("refs/heads/ok %s refs/heads/ok %s\n" % (self.B, self.A)
                 + "refs/heads/old %s refs/heads/old %s\n" % (self.A, ZERO)
                 + "(delete) %s refs/heads/gone %s\n" % (ZERO, self.A))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "report.json")
            with open(path, "wb") as handle:
                handle.write(encode(report))
            code, result, _ = run_check_json(
                ["--repo", self.fx.root, "--push-stdin", "--impact-file", path],
                stdin=lines.encode())
        snaps = result["snapshots"]
        dims = [s["checks"]["DOC_IMPACT_ASSESSMENT"] for s in snaps]
        self.assertEqual([d.get("status") for d in dims], ["PASS", "FAIL", None])
        self.assertTrue(dims[0]["base"]["independently_verified"])
        self.assertEqual(codes(dims[1]["diagnostics"], "fail"), ["SUBJECT_MISMATCH"])
        self.assertIs(dims[2]["assessed"], False)
        self.assertIn("ref deletion", dims[2]["reason"])
        self.assertEqual((code, result["summary"]["status"]), (1, "FAIL"))
        self.assertValidResult(result)


class Sha256RepositoryTests(DocopsTestCase):
    """The report's object ids follow the repository's hash: 64 digits in a sha256 repository."""

    def setUp(self):
        try:
            self.fx = GitFixture(object_format="sha256")
        except AssertionError:
            self.skipTest("this git cannot create sha256 repositories")
        self.addCleanup(self.fx.cleanup)
        self.A = self.fx.commit_files({"src/a.py": "a = 1\n"}, "A")
        self.fx.stage("src/a.py", "a = 2\n")
        self.B = self.fx.commit("B")
        self.tA = self.fx.git("rev-parse", self.A + "^{tree}")
        self.tB = self.fx.git("rev-parse", self.B + "^{tree}")
        self.assertEqual(len(self.tB), 64)

    def run_one(self, report, rev):
        repo = gitstate.open_repository(self.fx.root)
        request = {"kind": "index"} if rev == "index" else {"kind": "rev", "rev": rev}
        result = checks.check_request(repo, request, parse(report))
        self.assertIsNone(result["error"], result["error"])
        return result["checks"]["DOC_IMPACT_ASSESSMENT"]

    def test_a_64_digit_report_binds_to_a_commit_and_to_the_index(self):
        report = minimal_report(self.tA, self.tB, ["src/a.py"], evidence=[review("e1", self.tB)])
        for rev in (self.B, "index"):
            dim = self.run_one(report, rev)
            self.assertEqual((dim["status"], dim["subject"]["matches"]), ("PASS", True), rev)

    def test_a_40_digit_subject_never_matches_a_sha256_snapshot(self):
        report = minimal_report(self.tA, "c" * 40, ["src/a.py"], evidence=[review("e1", "c" * 40)])
        for rev in (self.B, "index"):
            dim = self.run_one(report, rev)
            self.assertEqual(dim["status"], "FAIL", rev)
            self.assertEqual(len(dim["subject"]["snapshot_tree"]), 64, rev)
            self.assertEqual(codes(dim["diagnostics"], "fail"), ["SUBJECT_MISMATCH"])

    def test_a_40_digit_base_is_not_a_tree_of_this_repository(self):
        report = minimal_report("c" * 40, self.tB, ["src/a.py"], evidence=[review("e1", self.tB)])
        dim = self.run_one(report, self.B)
        self.assertEqual((dim["status"], codes(dim["diagnostics"], "skip")),
                         ("SKIP", ["BASE_NOT_AVAILABLE"]))


class TreeHashTests(DocopsTestCase):
    """index_tree_oid must equal `git write-tree` for every index shape, or a report could
    never bind to (or could wrongly bind to) the index."""

    def compare(self, fx):
        repo = gitstate.open_repository(fx.root)
        entries = gitstate.index_snapshot(repo).entries
        self.assertEqual(gitstate.index_tree_oid(entries, 40), fx.git("write-tree"))

    def test_matches_write_tree_for_ordering_modes_links_and_unicode(self):
        with GitFixture() as fx:
            files = {"b.txt": "b\n", "a.txt": "a\n", "a/b.txt": "ab\n", "a/c/d.txt": "d\n",
                     "a.b": "ab\n", "a0": "0\n", "z/é.txt": "u\n", "z/Z.txt": "u\n",
                     "dir with space/f": "s\n"}
            for rel, content in files.items():
                fx.stage(rel, content)
            os.chmod(os.path.join(fx.root, "a.txt"), 0o755)
            fx.git("add", "--", "a.txt")
            os.symlink("a.txt", os.path.join(fx.root, "link"))
            fx.git("add", "--", "link")
            fx.git("update-index", "--add", "--cacheinfo", "160000,%s,sub" % ("1" * 40))
            self.compare(fx)

    def test_matches_write_tree_for_an_empty_index_and_a_single_file(self):
        with GitFixture() as fx:
            self.compare(fx)
            fx.stage("only", "x\n")
            self.compare(fx)

    def test_matches_write_tree_in_a_sha256_repository(self):
        tmp = tempfile.mkdtemp(prefix="docops-sha256-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        init = subprocess.run(["git", "-C", tmp, "init", "-q", "--object-format=sha256"],
                              capture_output=True, env=clean_env())
        if init.returncode != 0:
            self.skipTest("this git cannot create sha256 repositories")
        for rel, content in {"a.txt": "a\n", "d/e.txt": "e\n"}.items():
            os.makedirs(os.path.dirname(os.path.join(tmp, rel)) or tmp, exist_ok=True)
            with open(os.path.join(tmp, rel), "w") as handle:
                handle.write(content)
        subprocess.run(["git", "-C", tmp, "add", "."], check=True, env=clean_env())
        written = subprocess.run(["git", "-C", tmp, "write-tree"], capture_output=True, check=True,
                                 env=clean_env()).stdout.decode().strip()
        repo = gitstate.open_repository(tmp)
        entries = gitstate.index_snapshot(repo).entries
        self.assertEqual(len(written), 64)
        self.assertEqual(gitstate.index_tree_oid(entries, 64), written)
        self.assertEqual(gitstate.object_type(repo, written), "tree")
        # a 40-digit prefix of a real object is not that object's id here
        self.assertIsNone(gitstate.object_type(repo, written[:40]))
        self.assertIsNone(gitstate.object_type(repo, "ab" * 20))

    def test_a_tree_oid_lookup_distinguishes_absent_from_wrong_type(self):
        with GitFixture() as fx:
            commit = fx.commit_files({"f": "x\n"})
            repo = gitstate.open_repository(fx.root)
            self.assertEqual(gitstate.object_type(repo, commit), "commit")
            self.assertEqual(gitstate.object_type(repo, fx.git("rev-parse", "HEAD^{tree}")), "tree")
            self.assertEqual(gitstate.object_type(repo, fx.git("rev-parse", "HEAD:f")), "blob")
            self.assertIsNone(gitstate.object_type(repo, "5" * 40))
            with self.assertRaises(GitError):
                gitstate.object_type(repo, "HEAD")
            with self.assertRaises(GitError):
                gitstate.list_tree(repo, commit)


# --- F. result contract, failure modes, no mutation ----------------------------------------------

class ExitCodeTests(ImpactCase):
    """A required impact assessment that is missing, stale or unverifiable never exits 0 and
    exits 1 (the per-check status stays SKIP where it is a SKIP); exit 2 is only for a tool,
    usage or I/O error. Exit 3 keeps its Stage 1 meaning: SPEC applicability not established."""

    def setUp(self):
        super().setUp()
        self.C = self.commit_change({"src/a.py": "a = 3\n"}, "C")
        self.tC = self.tree(self.C)

    def run_report(self, report, *args):
        code, result, err = self.cli(report, *args)
        snap = result["snapshots"][0] if result["snapshots"] else None
        dim = snap["checks"]["DOC_IMPACT_ASSESSMENT"] if snap and snap["checks"] else None
        return code, result, dim

    def assertIncomplete(self, report, status, *args):
        code, result, dim = self.run_report(report, *args or ("--tree", "HEAD"))
        self.assertEqual((dim["status"], code), (status, 1), dim["diagnostics"])
        self.assertEqual(result["summary"]["exit_code"], 1)
        self.assertIsNotNone(result["summary"]["status"])  # a verdict, not a tool error
        self.assertEqual(result["summary"]["tool_errors"], 0)
        self.assertValidResult(result)
        return dim

    def test_stale_required_evidence_exits_1_and_keeps_the_skip_status(self):
        evidence = [review("e_old", self.tB, scope=("src/",)), review("e_all", self.tC)]
        report = minimal_report(self.tA, self.tC, ["src/a.py", "docs/a.md"], evidence=evidence)
        dim = self.assertIncomplete(report, "SKIP")
        self.assertEqual(dim["portions"][0]["evidence"][0]["freshness"], "stale")

    def test_unverifiable_required_evidence_exits_1(self):
        evidence = [review("e_gone", "9" * 40), review("e_all", self.tC)]
        report = minimal_report(self.tA, self.tC, ["src/a.py", "docs/a.md"], evidence=evidence)
        dim = self.assertIncomplete(report, "SKIP")
        self.assertEqual(dim["portions"][0]["evidence"][0]["freshness"], "unverifiable")

    def test_missing_required_evidence_exits_1(self):
        base = minimal_report(self.tA, self.tC, ["src/a.py", "docs/a.md"],
                              evidence=[review("e1", self.tC)])
        cited_nowhere = copy.deepcopy(base)
        cited_nowhere["minimal"]["evidence"] = ["not-in-the-list"]
        self.assertIncomplete(cited_nowhere, "FAIL")
        no_evidence = copy.deepcopy(base)
        del no_evidence["minimal"]["evidence"]
        self.assertIncomplete(no_evidence, "FAIL")
        category = full_report(self.tA, self.tC, {"architecture": ["src/a.py", "docs/a.md"]})
        del category["categories"]["tests"]["evidence"]
        self.assertIncomplete(category, "FAIL")
        gap = minimal_report(self.tA, self.tC, ["src/a.py", "docs/a.md"],
                             evidence=[review("e1", self.tC, scope=("src/",))])
        dim = self.assertIncomplete(gap, "SKIP")
        self.assertEqual(codes(dim["diagnostics"], "skip"), ["EVIDENCE_SCOPE_GAP"])

    def test_a_change_the_report_does_not_name_exits_1(self):
        dim = self.assertIncomplete(minimal_report(self.tA, self.tC, ["src/a.py"]), "SKIP")
        self.assertEqual(codes(dim["diagnostics"], "skip"), ["CHANGED_PATH_NOT_REPORTED"])

    def test_a_base_that_cannot_be_found_exits_1(self):
        report = self.good_minimal(subject=self.tC)
        report["base_tree"] = "1" * 40
        self.assertIncomplete(report, "SKIP")

    def test_an_old_tip_that_cannot_be_checked_exits_1(self):
        report = self.good_minimal(subject=self.tC)
        line = "refs/heads/x %s refs/heads/x %s\n" % (self.C, "7" * 40)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.json")
            with open(path, "wb") as handle:
                handle.write(encode(report))
            code, result, _ = run_check_json(
                ["--repo", self.fx.root, "--push-stdin", "--impact-file", path], stdin=line.encode())
        dim = result["snapshots"][0]["checks"]["DOC_IMPACT_ASSESSMENT"]
        self.assertEqual((dim["status"], code), ("SKIP", 1))

    def test_a_complete_assessment_still_exits_0(self):
        code, result, dim = self.run_report(self.good_minimal(subject=self.tC), "--tree", "HEAD")
        self.assertEqual((dim["status"], code, result["summary"]["exit_code"]), ("PASS", 0, 0))

    def test_tool_errors_stay_exit_2_and_are_distinct_from_incomplete_assessments(self):
        report = self.good_minimal(subject=self.tC)
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "nope.json")
            code, result, _ = run_check_json(["--repo", self.fx.root, "--tree", "HEAD",
                                              "--impact-file", missing])
            self.assertEqual((code, result["summary"]["status"]), (2, None))
            code, result, _ = run_check_json(["--repo", tmp, "--tree", "HEAD", "--impact-file", "-"],
                                             stdin=encode(report))
            self.assertEqual((code, result["summary"]["status"]), (2, None))
        code, result, _ = self.cli(report, "--tree", "no-such-revision")
        self.assertEqual((code, result["summary"]["status"]), (2, None))
        self.assertToolError(result["snapshots"][0], "SNAPSHOT_UNREADABLE")
        code, _, err = run_cli(["check", "--repo", self.fx.root, "--tree", "HEAD",
                                "--impact-file", "-", "--push-stdin"], stdin=b"")
        self.assertEqual(code, 2)
        repo = gitstate.open_repository(self.fx.root)
        with mock.patch.object(gitstate, "object_type", side_effect=GitError("boom")):
            result = checks.check_request(repo, {"kind": "rev", "rev": "HEAD"}, parse(report))
        self.assertToolError(result, "IMPACT_GIT_ERROR")
        # and none of the incomplete-assessment cases above is a tool error
        self.assertNotEqual(self.run_report(minimal_report(self.tA, self.tC, ["src/a.py"]),
                                            "--tree", "HEAD")[0], 2)

    def test_exit_3_keeps_its_stage_1_meaning_and_is_never_used_for_the_impact_dimension(self):
        D = self.commit_change({"docs/SPEC.md": "# unregistered nested\n"})
        tD = self.tree(D)
        passing = minimal_report(self.tA, tD, ["src/a.py", "docs/"], mode="spec_driven",
                                 evidence=[review("e1", tD)])
        code, result, _ = self.cli(passing, "--tree", "HEAD")
        snap = result["snapshots"][0]
        self.assertEqual((snap["checks"]["SPEC_STRUCTURE"]["status"],
                          snap["checks"]["DOC_IMPACT_ASSESSMENT"]["status"], code),
                         ("SKIP", "PASS", 3))
        incomplete = minimal_report(self.tA, tD, ["src/a.py"], mode="spec_driven",
                                    evidence=[review("e1", tD)])
        code, result, _ = self.cli(incomplete, "--tree", "HEAD")
        snap = result["snapshots"][0]
        self.assertEqual((snap["checks"]["SPEC_STRUCTURE"]["status"],
                          snap["checks"]["DOC_IMPACT_ASSESSMENT"]["status"], code),
                         ("SKIP", "SKIP", 1))
        for report in (passing, incomplete):
            self.assertNotEqual(self.cli(report, "--tree", "HEAD")[0], 2)

    def test_the_summary_exit_code_is_the_worst_of_the_snapshots(self):
        self.assertEqual(checks.combined_exit_code([]), 0)
        self.assertEqual(checks.combined_exit_code([0, 3]), 3)
        self.assertEqual(checks.combined_exit_code([3, 1, 0]), 1)
        self.assertEqual(checks.combined_exit_code([0, 0]), 0)
        snaps = [{"error": None, "status": "SKIP", "exit_code": 3},
                 {"error": None, "status": "SKIP", "exit_code": 1}]
        summary = checks.build_result("check", "/r", snaps)["summary"]
        self.assertEqual((summary["status"], summary["exit_code"]), ("SKIP", 1))


class ResultContractTests(ImpactCase):
    def test_without_a_report_the_dimension_is_not_assessed_as_before(self):
        code, result, _ = run_check_json(["--repo", self.fx.root, "--tree", "HEAD"])
        dim = result["snapshots"][0]["checks"]["DOC_IMPACT_ASSESSMENT"]
        self.assertEqual(set(dim), {"assessed", "reason"})
        self.assertIs(dim["assessed"], False)
        self.assertValidResult(result)

    def test_pass_fail_and_skip_results_conform_to_the_result_schema_and_exit_codes(self):
        cases = {
            "PASS": (self.good_minimal(), 0),
            "FAIL": (minimal_report(self.tA, self.tA, ["x"]), 1),
            "SKIP": (minimal_report(self.tA, self.tB, ["src/a.py"]), 1),
        }
        for status, (report, expected_exit) in cases.items():
            with self.subTest(status):
                code, result, err = self.cli(report, "--tree", "HEAD")
                dim = result["snapshots"][0]["checks"]["DOC_IMPACT_ASSESSMENT"]
                self.assertEqual((dim["status"], code, result["summary"]["exit_code"]),
                                 (status, expected_exit, expected_exit))
                self.assertEqual(result["snapshots"][0]["status"], status)
                self.assertEqual(err, "")
                self.assertValidResult(result)

    def test_snapshot_status_combines_spec_structure_and_impact_by_the_worst(self):
        self.commit_change({"SPEC.md": "# Broken\n\n## Goals\n\n- [ ] only a goal\n"}, "bad spec")
        tD = self.tree("HEAD")
        report = minimal_report(self.tA, tD, ["src/a.py", "docs/a.md", "SPEC.md"],
                                mode="spec_driven", evidence=[review("e1", tD)])
        code, result, _ = self.cli(report, "--tree", "HEAD")
        snap = result["snapshots"][0]
        self.assertEqual(snap["checks"]["DOC_IMPACT_ASSESSMENT"]["status"], "PASS")
        self.assertEqual(snap["checks"]["SPEC_STRUCTURE"]["status"], "FAIL")
        self.assertEqual((snap["status"], code), ("FAIL", 1))

    def test_na_spec_and_passing_impact_report_is_a_pass_never_na(self):
        code, result, _ = self.cli(self.good_minimal(), "--tree", "HEAD")
        snap = result["snapshots"][0]
        self.assertEqual(snap["checks"]["SPEC_STRUCTURE"]["status"], "N/A")
        self.assertEqual((snap["status"], code), ("PASS", 0))

    def test_the_impact_dimension_is_never_na(self):
        broken = {"schema_version": 1}
        for report in (self.good_minimal(), broken, b"", minimal_report(self.tA, self.tA, ["x"])):
            dim = self.dim(report)
            self.assertIn(dim["status"], ("PASS", "FAIL", "SKIP"))

    def test_report_identity_is_recorded(self):
        raw = encode(self.good_minimal())
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.json")
            with open(path, "wb") as handle:
                handle.write(raw)
            code, result, _ = run_check_json(["--repo", self.fx.root, "--tree", "HEAD",
                                              "--impact-file", path])
        report = result["snapshots"][0]["checks"]["DOC_IMPACT_ASSESSMENT"]["report"]
        self.assertEqual(report, {"source": "file", "path": path,
                                  "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})

    def test_malformed_report_is_a_fail_of_the_dimension_with_exit_1(self):
        for raw in (b"", b"{", b"[]", json.dumps({"schema_version": 1}).encode()):
            with self.subTest(raw[:20]):
                code, result, _ = self.cli(raw, "--tree", "HEAD")
                dim = result["snapshots"][0]["checks"]["DOC_IMPACT_ASSESSMENT"]
                self.assertEqual((dim["status"], code), ("FAIL", 1))
                self.assertEqual(dim["portions"], [])
                self.assertValidResult(result)

    def test_unreadable_report_is_a_tool_failure_not_a_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, path in (("missing", os.path.join(tmp, "nope.json")), ("directory", tmp)):
                with self.subTest(name):
                    code, result, _ = run_check_json(
                        ["--repo", self.fx.root, "--tree", "HEAD", "--impact-file", path])
                    self.assertEqual(code, 2)
                    self.assertIsNone(result["summary"]["status"])
                    self.assertEqual(result["summary"]["tool_errors"], 1)
                    self.assertTrue(result["summary"]["reason"].startswith(
                        "impact report could not be read"), result["summary"]["reason"])
                    self.assertEqual(result["snapshots"], [])
                    self.assertValidResult(result)
            if os.geteuid() != 0:
                locked = os.path.join(tmp, "locked.json")
                with open(locked, "wb") as handle:
                    handle.write(encode(self.good_minimal()))
                os.chmod(locked, 0)
                code, result, _ = run_check_json(
                    ["--repo", self.fx.root, "--tree", "HEAD", "--impact-file", locked])
                self.assertEqual((code, result["summary"]["status"]), (2, None))

    def test_an_oversized_report_file_is_a_fail_not_a_truncated_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "huge.json")
            with open(path, "wb") as handle:
                handle.write(b" " * (impact.MAX_REPORT_BYTES + 10))
            code, result, _ = run_check_json(
                ["--repo", self.fx.root, "--tree", "HEAD", "--impact-file", path])
        dim = result["snapshots"][0]["checks"]["DOC_IMPACT_ASSESSMENT"]
        self.assertEqual((code, dim["status"]), (1, "FAIL"))
        self.assertIn("REPORT_TOO_LARGE", codes(dim["diagnostics"]))

    def test_there_is_no_record_or_write_subcommand(self):
        for name in ("record", "write", "generate"):
            code, out, err = run_cli([name, "--repo", self.fx.root, "--impact-file", "-"], stdin=b"{}")
            self.assertEqual((code, out), (2, ""), name)
            self.assertIn("invalid choice", err)

    def test_two_readers_of_standard_input_are_refused(self):
        code, out, err = run_cli(["check", "--repo", self.fx.root, "--push-stdin",
                                  "--impact-file", "-"], stdin=b"")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("both read standard input", err)

    def test_git_failure_while_validating_is_a_tool_error_never_a_pass(self):
        repo = gitstate.open_repository(self.fx.root)
        parsed = parse(self.good_minimal())
        with mock.patch.object(gitstate, "object_type", side_effect=GitError("boom")):
            result = checks.check_request(repo, {"kind": "rev", "rev": "HEAD"}, parsed)
        self.assertToolError(result, "IMPACT_GIT_ERROR")
        with mock.patch.object(impact, "assess", side_effect=RuntimeError("bug")):
            result = checks.check_request(repo, {"kind": "rev", "rev": "HEAD"}, parsed)
        self.assertToolError(result, "INTERNAL_ERROR")
        document = checks.build_result("check", self.fx.root, [result])
        self.assertEqual((document["summary"]["status"], document["summary"]["exit_code"]), (None, 2))

    def test_validation_changes_nothing_in_the_repository_or_the_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.json")
            with open(path, "wb") as handle:
                handle.write(encode(self.good_minimal()))
            self.fx.write("src/a.py", "unstaged\n")
            before_repo, before_report = tree_digest(self.fx.root), tree_digest(tmp)
            code, result, _ = run_check_json(
                ["--repo", self.fx.root, "--tree", "HEAD", "--tree", "index", "--impact-file", path])
            self.assertEqual(code, 0)  # the unstaged edit is invisible to both snapshots
            self.assertEqual([s["checks"]["DOC_IMPACT_ASSESSMENT"]["status"]
                              for s in result["snapshots"]], ["PASS", "PASS"])
            rcode, out, _ = run_cli(["report", "--repo", self.fx.root, "--tree", "HEAD",
                                     "--impact-file", path])
            self.assertEqual(rcode, 0)
            self.assertIn("DOC_IMPACT_ASSESSMENT    PASS", out)
            self.assertEqual(tree_digest(self.fx.root), before_repo)
            self.assertEqual(tree_digest(tmp), before_report)

    def test_text_report_shows_status_portions_and_the_semantic_limit_safely(self):
        report = full_report(self.tA, self.tB, {"architecture": ["src/a.py", "docs/a.md"]})
        report["\x1b[31mred"] = 1  # an unknown key whose name tries to reach the terminal
        code, out, err = run_cli(["report", "--repo", self.fx.root, "--tree", "HEAD",
                                  "--impact-file", "-"], stdin=encode(report))
        self.assertEqual(code, 1)
        self.assertIn("DOC_IMPACT_ASSESSMENT    FAIL", out)
        self.assertIn("semantic review: not established", out)
        self.assertNotIn("\x1b", out)
        self.assertIn("\\x1b[31mred", out)
        ok_code, ok_out, _ = run_cli(["report", "--repo", self.fx.root, "--tree", "HEAD",
                                      "--impact-file", "-"],
                                     stdin=encode(full_report(self.tA, self.tB,
                                                              {"architecture": ["src/a.py", "docs/a.md"]})))
        self.assertEqual(ok_code, 0)
        self.assertIn("mode direct_fix, profile full", ok_out)
        self.assertIn("NOT independently verified", ok_out)
        self.assertNotIn("verified against the old tip", ok_out)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.json")
            with open(path, "wb") as handle:
                handle.write(encode(self.good_minimal()))
            line = "refs/heads/x %s refs/heads/x %s\n" % (self.B, self.A)
            _, pushed, _ = run_cli(["report", "--repo", self.fx.root, "--push-stdin",
                                    "--impact-file", path], stdin=line.encode())
        self.assertIn("verified against the old tip of the push", pushed)
        self.assertNotIn("NOT independently verified", pushed)
        self.assertIn("portion architecture: PASS", ok_out)
        self.assertIn("portion decisions: PASS", ok_out)


if __name__ == "__main__":
    unittest.main()
