"""ADR-0023: a commit or push check requires an impact report when code changed.

Two layers. First the decision itself, a pure function over a finite set of inputs, is checked against a
complete table (mode x classification x code change x report state: every row asserts the status and the
reason code). Then the real command line runs on throwaway repositories: commit mode, push mode, and the
failures that must never read as "no signal". The existing validator scenarios (ADR-0014) are in test_impact.py.
"""
import json
import os
import shutil
import unittest

from helpers import DocopsTestCase, GitFixture, run_check_json, tree_digest

from docops_core import impact

ZERO = "0" * 40
REQUIRED = "IMPACT_REPORT_REQUIRED"
UNCLASSIFIED = "IMPACT_CLASSIFICATION_FAILED"

# mode (commit|push|other) / classification (ok|bad) / code change (code|nocode) / report (none or its status)
#   -> status (- = no signal) and reason code
TABLE = """
commit ok  code   none -> FAIL IMPACT_REPORT_REQUIRED
commit ok  code   PASS -> PASS -
commit ok  code   SKIP -> SKIP -
commit ok  code   FAIL -> FAIL -
commit ok  nocode none -> -    -
commit ok  nocode PASS -> PASS -
commit ok  nocode SKIP -> SKIP -
commit ok  nocode FAIL -> FAIL -
commit bad code   none -> FAIL IMPACT_CLASSIFICATION_FAILED
commit bad code   PASS -> PASS -
commit bad code   SKIP -> SKIP -
commit bad code   FAIL -> FAIL -
commit bad nocode none -> FAIL IMPACT_CLASSIFICATION_FAILED
commit bad nocode PASS -> PASS -
commit bad nocode SKIP -> SKIP -
commit bad nocode FAIL -> FAIL -
push   ok  code   none -> FAIL IMPACT_REPORT_REQUIRED
push   ok  code   PASS -> PASS -
push   ok  code   SKIP -> SKIP -
push   ok  code   FAIL -> FAIL -
push   ok  nocode none -> -    -
push   ok  nocode PASS -> PASS -
push   ok  nocode SKIP -> SKIP -
push   ok  nocode FAIL -> FAIL -
push   bad code   none -> FAIL IMPACT_CLASSIFICATION_FAILED
push   bad code   PASS -> PASS -
push   bad code   SKIP -> SKIP -
push   bad code   FAIL -> FAIL -
push   bad nocode none -> FAIL IMPACT_CLASSIFICATION_FAILED
push   bad nocode PASS -> PASS -
push   bad nocode SKIP -> SKIP -
push   bad nocode FAIL -> FAIL -
other  ok  code   none -> -    -
other  ok  code   PASS -> PASS -
other  ok  code   SKIP -> SKIP -
other  ok  code   FAIL -> FAIL -
other  ok  nocode none -> -    -
other  ok  nocode PASS -> PASS -
other  ok  nocode SKIP -> SKIP -
other  ok  nocode FAIL -> FAIL -
other  bad code   none -> -    -
other  bad code   PASS -> PASS -
other  bad code   SKIP -> SKIP -
other  bad code   FAIL -> FAIL -
other  bad nocode none -> -    -
other  bad nocode PASS -> PASS -
other  bad nocode SKIP -> SKIP -
other  bad nocode FAIL -> FAIL -
"""

# path -> class (None = cannot be classified)
PATHS = {
    "src/a.py": "code", "scripts/run.sh": "code", "schemas/x.json": "code", "MANIFEST.txt": "code",
    "Makefile": "code", ".github/workflows/checks.yml": "code", "README.md": "document",
    "docs/adr/0001-x.md": "document", "SKILL.MD": "document", "notes.Md": "document",
    "docs/readme.md.bak": "code", "md": "code", ".md": "document", "a b/c d.py": "code",
    "ünï/cödé.py": "code",
    "": None, "a\nb.py": None, "a\tb.py": None, "a\x7fb.py": None, "bad\udcffname.py": None,
}


def parse_table():
    rows = {}
    for line in TABLE.strip().splitlines():
        left, right = line.split("->")
        key = tuple(left.split())
        status, code = right.split()
        rows[key] = (None if status == "-" else status, None if code == "-" else code)
    return rows


class DecisionTableTests(unittest.TestCase):
    def test_the_table_has_every_combination_exactly_once(self):
        rows = [tuple(line.split("->")[0].split()) for line in TABLE.strip().splitlines()]
        full = {(m, c, k, r) for m in ("commit", "push", "other") for c in ("ok", "bad")
                for k in ("code", "nocode") for r in ("none", "PASS", "SKIP", "FAIL")}
        self.assertEqual(len(rows), 48)
        self.assertEqual(set(rows), full)

    def test_each_row_gives_its_status_and_reason_code(self):
        for (mode, cls, code, report), want in sorted(parse_table().items()):
            with self.subTest(mode=mode, classified=cls, code=code, report=report):
                got = impact.outcome(mode, cls == "ok", code == "code", None if report == "none" else report)
                self.assertEqual(got, want)

    def test_path_classes(self):
        for path, want in PATHS.items():
            with self.subTest(path=path):
                self.assertEqual(impact.classify_path(path), want)


class SignalCase(DocopsTestCase):
    """History: base commit with src/a.py, docs/a.md and README.md."""

    def setUp(self):
        self.fx = GitFixture()
        self.addCleanup(self.fx.cleanup)
        self.base = self.fx.commit_files({"README.md": "r\n", "src/a.py": "a = 1\n", "docs/a.md": "d\n"}, "base")

    def run_check(self, *args, stdin=b""):
        return run_check_json(["--repo", self.fx.root, *args], stdin=stdin)

    def dim(self, result, index=0):
        return result["snapshots"][index]["checks"]["DOC_IMPACT_ASSESSMENT"]

    def tree_of(self, rev):
        return self.fx.git("rev-parse", rev + "^{tree}")

    def report_path(self, base_tree, subject_tree):
        common = self.fx.git("rev-parse", "--git-common-dir")
        common = common if os.path.isabs(common) else os.path.join(self.fx.root, common)
        return os.path.join(common, "docops-impact", "%s_%s.json" % (base_tree, subject_tree))

    def write_report(self, base_tree, subject_tree, paths, evidence_tree=None, scope=(".",), raw=None):
        path = self.report_path(base_tree, subject_tree)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        report = {"schema_version": 1, "mode": "direct_fix",
                  "process": {"spec_skill_invoked": False, "spec_created": False, "plan_created": False},
                  "base_tree": base_tree, "subject_tree": subject_tree, "profile": "minimal",
                  "minimal": {"affected_paths": paths, "reason": "one line changed; no category is affected",
                              "evidence": ["e1"]},
                  "evidence": [{"id": "e1", "kind": "review", "tree": evidence_tree or subject_tree,
                                "scope_paths": list(scope), "outcome": "pass", "summary": "read the change"}]}
        with open(path, "wb") as handle:
            handle.write(raw if raw is not None else json.dumps(report).encode("utf-8"))
        return path

    def stage_code(self, content="a = 2\n"):
        self.fx.stage("src/a.py", content)
        return self.fx.git("write-tree")  # the tree of the index, so evidence and report can name it

    def push_line(self, local, remote, ref="refs/heads/main"):
        return ("%s %s %s %s\n" % (ref, local, ref, remote)).encode()

    def commit_code(self, content="a = 2\n"):
        self.fx.stage("src/a.py", content)
        return self.fx.commit("code")

    def assertRequired(self, code, result, paths=()):
        self.assertEqual(code, 1, result)
        self.assertValidResult(result)
        dim = self.dim(result)
        self.assertEqual((dim["assessed"], dim["status"]), (True, "FAIL"))
        diag = dim["diagnostics"]
        self.assertEqual([d["code"] for d in diag], [REQUIRED])
        for path in paths:
            self.assertIn(path, diag[0]["message"])
        return diag[0]["message"]


class TableThroughTheCliTests(SignalCase):
    """Every reachable row of the table, run through the real command line in both modes. A row with no
    code change and a report only arises with an explicit --impact-file: the stored report is looked up
    only when a code path or an unclassifiable path changed."""

    def scenario(self, mode, kind, report):
        """Prepare one change; return the argument list and stdin of the check."""
        self.fx.git("reset", "-q", "--hard", self.base)
        shutil.rmtree(os.path.dirname(self.report_path("0", "0")), ignore_errors=True)  # a report is found by its trees
        name = {"code": "src/a.py", "nocode": "docs/a.md", "bad": "odd\nname.py"}[kind]
        if mode == "commit":
            self.fx.stage(name, "v2\n")
            subject = self.fx.git("write-tree")
            args, stdin = ["--tree", "index"], b""
        else:
            self.fx.stage(name, "v2\n")
            tip = self.fx.commit("change")
            subject = self.tree_of(tip)
            args, stdin = ["--push-stdin"], self.push_line(tip, self.base)
        base = self.tree_of(self.base)
        if report == "PASS":
            self.write_report(base, subject, [name])
        elif report == "SKIP":
            self.write_report(base, subject, [name], evidence_tree=base, scope=(os.path.dirname(name) + "/",))
        elif report == "FAIL":
            self.write_report(base, subject, [], raw=b"not json")
        return args, stdin

    def test_reachable_rows_match_the_table(self):
        rows = parse_table()
        for mode in ("commit", "push"):
            for kind, report in (("code", "none"), ("code", "PASS"), ("code", "SKIP"), ("code", "FAIL"),
                                 ("nocode", "none"), ("bad", "none")):
                with self.subTest(mode=mode, change=kind, report=report):
                    args, stdin = self.scenario(mode, kind, report)
                    code, result, _ = self.run_check(*args, stdin=stdin)
                    dim = self.dim(result)
                    got = ((dim["status"], dim["diagnostics"][0]["code"] if dim["diagnostics"]
                            and dim["diagnostics"][0]["code"].startswith("IMPACT_") else None)
                           if dim.get("assessed") else (None, None))
                    want = rows[(mode, "bad" if kind == "bad" else "ok", "nocode" if kind == "nocode" else "code",
                                 report)]
                    self.assertEqual(got, want)


class CommitModeTests(SignalCase):
    def test_code_changed_and_no_report_fails_with_a_command_to_the_agent(self):
        tree = self.stage_code()
        code, result, _ = self.run_check("--tree", "index")
        message = self.assertRequired(code, result, ["src/a.py"])
        self.assertEqual(result["summary"], {"status": "FAIL", "exit_code": 1, "tool_errors": 0})
        for needle in ("architecture", "interfaces", "failure_behaviour", "configuration", "tests", "decisions",
                       "acceptance", "planning_and_operations", "schemas/docops-impact-report.schema.json",
                       "minimal", "repeat", self.report_path(self.tree_of("HEAD"), tree)):
            self.assertIn(needle, message)

    def test_the_other_dimensions_and_the_spec_structure_keep_their_own_status(self):
        self.stage_code()
        _, result, _ = self.run_check("--tree", "index")
        checks = result["snapshots"][0]["checks"]
        self.assertEqual(checks["SPEC_STRUCTURE"]["status"], "N/A")
        for name in ("TESTS", "DOC_CONSISTENCY_REVIEW", "ACCEPTANCE_VERIFICATION", "PUBLICATION", "OWNER_ACCEPTANCE"):
            self.assertIs(checks[name]["assessed"], False)

    def test_a_docs_only_change_gives_no_signal(self):
        self.fx.stage("docs/a.md", "d v2\n")
        code, result, _ = self.run_check("--tree", "index")
        self.assertEqual(code, 0, result)
        self.assertIs(self.dim(result)["assessed"], False)

    def test_an_unchanged_index_gives_no_signal(self):
        code, result, _ = self.run_check("--tree", "index")
        self.assertEqual(code, 0, result)
        self.assertIs(self.dim(result)["assessed"], False)

    def test_a_valid_report_for_the_exact_change_passes(self):
        tree = self.stage_code()
        self.write_report(self.tree_of("HEAD"), tree, ["src/a.py"])
        code, result, _ = self.run_check("--tree", "index")
        self.assertEqual(code, 0, result)
        dim = self.dim(result)
        self.assertEqual(dim["status"], "PASS")
        self.assertTrue(dim["base"]["independently_verified"])
        self.assertValidResult(result)

    def test_a_report_with_stale_evidence_is_skip_not_required(self):
        tree = self.stage_code()
        # evidence gathered on the base tree, scope src/, which the change touched
        self.write_report(self.tree_of("HEAD"), tree, ["src/a.py"], evidence_tree=self.tree_of("HEAD"), scope=("src/",))
        code, result, _ = self.run_check("--tree", "index")
        self.assertEqual(code, 1, result)
        dim = self.dim(result)
        self.assertEqual(dim["status"], "SKIP")
        self.assertNotIn(REQUIRED, [d["code"] for d in dim["diagnostics"]])
        self.assertEqual(dim["portions"][0]["evidence"][0]["freshness"], "stale")

    def test_a_malformed_report_is_a_fail_not_required(self):
        tree = self.stage_code()
        self.write_report(self.tree_of("HEAD"), tree, [], raw=b"not json")
        code, result, _ = self.run_check("--tree", "index")
        self.assertEqual(code, 1, result)
        dim = self.dim(result)
        self.assertEqual(dim["status"], "FAIL")
        self.assertEqual([d["code"] for d in dim["diagnostics"]], ["REPORT_NOT_JSON"])

    def test_a_report_that_names_another_base_fails(self):
        self.fx.stage("docs/alt.md", "alt\n")
        alt = self.fx.git("write-tree")  # a real tree that is not the tree of HEAD
        self.fx.git("reset", "-q")
        tree = self.stage_code()
        self.write_report(alt, tree, ["src/a.py"])
        # stored under the key of the true base, naming another one inside
        os.replace(self.report_path(alt, tree), self.report_path(self.tree_of("HEAD"), tree))
        code, result, _ = self.run_check("--tree", "index")
        self.assertEqual(code, 1, result)
        self.assertIn("BASE_MISMATCH", [d["code"] for d in self.dim(result)["diagnostics"]])

    def test_a_change_of_the_index_after_the_report_needs_a_new_report(self):
        tree = self.stage_code()
        self.write_report(self.tree_of("HEAD"), tree, ["src/a.py"])
        self.assertEqual(self.run_check("--tree", "index")[0], 0)
        self.stage_code("a = 3\n")  # another subject tree: the old report is not looked up for it
        code, result, _ = self.run_check("--tree", "index")
        self.assertRequired(code, result, ["src/a.py"])

    def test_an_explicit_report_wins_over_the_stored_one(self):
        tree = self.stage_code()
        stored = self.write_report(self.tree_of("HEAD"), tree, ["src/a.py"], raw=b"not json")
        explicit = os.path.join(os.path.dirname(stored), "explicit.json")
        self.write_report(self.tree_of("HEAD"), tree, ["src/a.py"])
        os.replace(stored, explicit)  # the valid report moves to the explicit path
        self.write_report(self.tree_of("HEAD"), tree, ["src/a.py"], raw=b"not json")  # the stored one is broken
        code, result, _ = self.run_check("--tree", "index", "--impact-file", explicit)
        self.assertEqual(code, 0, result)
        self.assertEqual(self.dim(result)["status"], "PASS")

    def test_a_revision_check_is_unchanged(self):
        self.commit_code()
        code, result, _ = self.run_check("--tree", "HEAD")
        self.assertEqual(code, 0, result)
        self.assertIs(self.dim(result)["assessed"], False)

    def test_the_message_lists_the_changed_code_paths_up_to_a_bound(self):
        for number in range(60):
            self.fx.stage("src/f%02d.py" % number, "x\n")
        self.fx.stage("docs/b.md", "doc\n")
        code, result, _ = self.run_check("--tree", "index")
        message = self.assertRequired(code, result, ["src/f00.py", "src/f49.py"])
        self.assertNotIn("src/f59.py", message)
        self.assertNotIn("docs/b.md", message)
        self.assertIn("60 code path(s)", message)
        self.assertIn("+10 more", message)

    def test_an_unborn_head_is_compared_with_the_empty_tree(self):
        with GitFixture() as fresh:
            fresh.stage("src/a.py", "a\n")
            code, result, _ = run_check_json(["--repo", fresh.root, "--tree", "index"])
            self.assertRequired(code, result, ["src/a.py"])

    def test_a_path_that_cannot_be_classified_fails_with_its_own_reason(self):
        self.fx.stage("odd\nname.py", "x\n")
        code, result, _ = self.run_check("--tree", "index")
        self.assertEqual(code, 1, result)
        dim = self.dim(result)
        self.assertEqual((dim["status"], [d["code"] for d in dim["diagnostics"]]), ("FAIL", [UNCLASSIFIED]))
        self.assertValidResult(result)

    def test_an_unmerged_index_fails_with_its_own_reason(self):
        blob = self.fx.git("hash-object", "-w", "--stdin", input_bytes=b"x\n")
        self.fx.git("update-index", "--index-info",
                    input_bytes=("100644 %s 1\tsrc/a.py\n100644 %s 2\tsrc/a.py\n" % (blob, blob)).encode())
        code, result, _ = self.run_check("--tree", "index")
        self.assertEqual(code, 1, result)
        dim = self.dim(result)
        self.assertEqual((dim["status"], [d["code"] for d in dim["diagnostics"]]), ("FAIL", ["IMPACT_SUBJECT_UNAVAILABLE"]))
        self.assertValidResult(result)

    def test_an_unmerged_index_with_documents_only_gives_no_signal(self):
        blob = self.fx.git("hash-object", "-w", "--stdin", input_bytes=b"x\n")
        self.fx.git("update-index", "--index-info",
                    input_bytes=("100644 %s 1\tdocs/a.md\n100644 %s 2\tdocs/a.md\n" % (blob, blob)).encode())
        code, result, _ = self.run_check("--tree", "index")
        self.assertEqual(code, 0, result)
        self.assertIs(self.dim(result)["assessed"], False)

    def test_the_same_snapshot_gives_the_same_result_and_nothing_is_written(self):
        self.stage_code()
        before = tree_digest(self.fx.root)
        first = self.run_check("--tree", "index")
        second = self.run_check("--tree", "index")
        self.assertEqual(first, second)
        self.assertEqual(tree_digest(self.fx.root), before)


class PushModeTests(SignalCase):
    def setUp(self):
        super().setUp()
        self.old = self.base

    def test_code_changed_and_no_report_fails_for_the_pushed_ref(self):
        new = self.commit_code()
        code, result, _ = self.run_check("--push-stdin", stdin=self.push_line(new, self.old))
        message = self.assertRequired(code, result, ["src/a.py"])
        self.assertIn(self.report_path(self.tree_of(self.old), self.tree_of(new)), message)

    def test_a_docs_only_push_gives_no_signal(self):
        self.fx.stage("docs/a.md", "d v2\n")
        new = self.fx.commit("docs")
        code, result, _ = self.run_check("--push-stdin", stdin=self.push_line(new, self.old))
        self.assertEqual(code, 0, result)
        self.assertIs(self.dim(result)["assessed"], False)

    def test_a_valid_report_for_the_pushed_change_passes(self):
        new = self.commit_code()
        self.write_report(self.tree_of(self.old), self.tree_of(new), ["src/a.py"])
        code, result, _ = self.run_check("--push-stdin", stdin=self.push_line(new, self.old))
        self.assertEqual(code, 0, result)
        dim = self.dim(result)
        self.assertEqual(dim["status"], "PASS")
        self.assertTrue(dim["base"]["independently_verified"])

    def test_a_stale_report_for_the_pushed_change_is_skip(self):
        new = self.commit_code()
        self.write_report(self.tree_of(self.old), self.tree_of(new), ["src/a.py"],
                          evidence_tree=self.tree_of(self.old), scope=("src/",))
        code, result, _ = self.run_check("--push-stdin", stdin=self.push_line(new, self.old))
        self.assertEqual(code, 1, result)
        self.assertEqual(self.dim(result)["status"], "SKIP")

    def test_a_new_ref_is_compared_with_the_parent_of_its_oldest_unpublished_commit(self):
        first = self.commit_code("a = 2\n")
        second = self.commit_code("a = 3\n")
        self.fx.git("update-ref", "refs/remotes/origin/main", self.base)
        code, result, _ = self.run_check("--push-stdin", stdin=self.push_line(second, ZERO, "refs/heads/new"))
        message = self.assertRequired(code, result, ["src/a.py"])
        self.assertIn(self.report_path(self.tree_of(self.base), self.tree_of(second)), message)
        self.assertNotEqual(first, second)

    def test_a_new_ref_that_a_remote_tracking_ref_already_holds_gives_no_signal(self):
        new = self.commit_code()
        self.fx.git("update-ref", "refs/remotes/origin/main", new)
        code, result, _ = self.run_check("--push-stdin", stdin=self.push_line(new, ZERO, "refs/heads/new"))
        self.assertEqual(code, 0, result)
        self.assertIs(self.dim(result)["assessed"], False)

    def test_a_deleted_ref_is_ignored(self):
        code, result, _ = self.run_check("--push-stdin", stdin=self.push_line(ZERO, self.old))
        self.assertEqual(code, 0, result)
        self.assertEqual(result["snapshots"][0]["request"]["kind"], "deleted-ref")

    def test_several_refs_are_judged_one_by_one(self):
        code_commit = self.commit_code()
        self.fx.git("checkout", "-q", "-b", "docs", self.base)
        self.fx.stage("docs/a.md", "d v2\n")
        docs_commit = self.fx.commit("docs")
        stdin = (self.push_line(code_commit, self.old, "refs/heads/main")
                 + self.push_line(docs_commit, self.old, "refs/heads/docs")
                 + self.push_line(ZERO, self.old, "refs/heads/gone"))
        code, result, _ = self.run_check("--push-stdin", stdin=stdin)
        self.assertEqual(code, 1, result)
        self.assertEqual([s["exit_code"] for s in result["snapshots"]], [1, 0, 0])
        self.assertEqual([s["checks"]["DOC_IMPACT_ASSESSMENT"].get("status") for s in result["snapshots"]],
                         ["FAIL", None, None])

    def test_zero_byte_stdin_keeps_its_current_handling(self):
        code, result, _ = self.run_check("--push-stdin", stdin=b"")
        self.assertEqual(code, 2, result)
        self.assertIn("received no ref updates", result["summary"]["reason"])

    def test_an_old_tip_that_is_not_in_the_repository_is_a_tool_error(self):
        new = self.commit_code()
        code, result, _ = self.run_check("--push-stdin", stdin=self.push_line(new, "1" * 40))
        self.assertEqual(code, 2, result)
        self.assertEqual(result["snapshots"][0]["error"]["code"], "IMPACT_BASE_UNAVAILABLE")

    def test_an_unclassifiable_path_in_a_pushed_change_fails_with_its_own_reason(self):
        self.fx.stage("odd\nname.py", "x\n")
        new = self.fx.commit("odd")
        code, result, _ = self.run_check("--push-stdin", stdin=self.push_line(new, self.old))
        self.assertEqual(code, 1, result)
        self.assertEqual([d["code"] for d in self.dim(result)["diagnostics"]], [UNCLASSIFIED])

    def test_the_push_input_reaches_the_check_as_it_was_sent(self):
        new = self.commit_code()
        old = self.old
        stdin = ("refs/heads/a b %s refs/heads/c d %s\n" % (new, old)).encode()
        _, result, _ = self.run_check("--push-stdin", stdin=stdin)
        self.assertEqual(result["snapshots"][0]["request"]["kind"], "invalid-update")  # six fields: refused as before
        stdin = self.push_line(new, old, "refs/heads/odd")
        _, result, _ = self.run_check("--push-stdin", stdin=stdin)
        request = result["snapshots"][0]["request"]
        self.assertEqual((request["local_ref"], request["local_oid"], request["remote_ref"], request["remote_oid"]),
                         ("refs/heads/odd", new, "refs/heads/odd", old))

    def test_a_new_annotated_tag_on_a_published_commit_gives_no_signal(self):
        new = self.commit_code()
        self.fx.git("update-ref", "refs/remotes/origin/main", new)
        self.fx.git("tag", "-a", "-m", "release", "v1", new)
        tag = self.fx.git("rev-parse", "refs/tags/v1")
        self.assertNotEqual(tag, new)  # the pushed object is the tag object
        code, result, _ = self.run_check("--push-stdin", stdin=self.push_line(tag, ZERO, "refs/tags/v1"))
        self.assertEqual(code, 0, result)
        self.assertIs(self.dim(result)["assessed"], False)

    def test_an_updated_annotated_tag_without_a_code_change_gives_no_signal(self):
        self.fx.git("tag", "-a", "-m", "one", "v1", self.old)
        first = self.fx.git("rev-parse", "refs/tags/v1")
        self.fx.git("tag", "-f", "-a", "-m", "two", "v1", self.old)
        second = self.fx.git("rev-parse", "refs/tags/v1")
        code, result, _ = self.run_check("--push-stdin", stdin=self.push_line(second, first, "refs/tags/v1"))
        self.assertEqual(code, 0, result)
        self.assertIs(self.dim(result)["assessed"], False)

    def test_an_annotated_tag_on_unpublished_code_needs_a_report(self):
        new = self.commit_code()
        self.fx.git("update-ref", "refs/remotes/origin/main", self.old)
        self.fx.git("tag", "-a", "-m", "release", "v1", new)
        tag = self.fx.git("rev-parse", "refs/tags/v1")
        code, result, _ = self.run_check("--push-stdin", stdin=self.push_line(tag, ZERO, "refs/tags/v1"))
        self.assertRequired(code, result, ["src/a.py"])

    def test_a_merge_of_branches_from_two_published_bases_reports_more_but_never_less(self):
        # Two published commits with different trees; the unpublished merge joins a branch from each. The
        # base is the parent of the first unpublished root, so one published file of the other side is
        # reported too (over-reporting), and both unpublished files are reported (nothing is missed).
        self.fx.git("checkout", "-q", "-b", "p1", self.base)
        self.fx.stage("src/p1.py", "p1\n")
        p1 = self.fx.commit("published 1")
        self.fx.git("checkout", "-q", "-b", "p2", self.base)
        self.fx.stage("src/p2.py", "p2\n")
        p2 = self.fx.commit("published 2")
        self.fx.git("update-ref", "refs/remotes/origin/p1", p1)
        self.fx.git("update-ref", "refs/remotes/origin/p2", p2)
        self.fx.git("checkout", "-q", "-b", "x", p1)
        self.fx.stage("lib/x.py", "x\n")
        self.fx.commit("x")
        self.fx.git("checkout", "-q", "-b", "y", p2)
        self.fx.stage("lib/y.py", "y\n")
        self.fx.commit("y")
        self.fx.git("merge", "-q", "--no-edit", "x")
        merge = self.fx.git("rev-parse", "HEAD")
        code, result, _ = self.run_check("--push-stdin", stdin=self.push_line(merge, ZERO, "refs/heads/m"))
        message = self.assertRequired(code, result, ["lib/x.py", "lib/y.py"])
        self.assertEqual(sum(name in message for name in ("src/p1.py", "src/p2.py")), 1)  # one side, over-reported

    def test_a_missing_head_commit_is_a_tool_error_and_not_an_unborn_repository(self):
        head = self.fx.git("rev-parse", "HEAD")
        os.remove(os.path.join(self.fx.root, ".git", "objects", head[:2], head[2:]))
        self.fx.stage("src/a.py", "a = 2\n")
        code, result, _ = self.run_check("--tree", "index")
        self.assertEqual(code, 2, result)
        self.assertEqual(result["snapshots"][0]["error"]["code"], "IMPACT_GIT_ERROR")

    def test_a_stored_report_that_is_not_a_regular_file_is_a_tool_error(self):
        tree = self.stage_code()
        path = self.report_path(self.tree_of("HEAD"), tree)
        os.makedirs(path)  # a directory where the report should be
        code, result, _ = self.run_check("--tree", "index")
        self.assertEqual(code, 2, result)
        self.assertEqual(result["snapshots"][0]["error"]["code"], "IMPACT_REPORT_UNREADABLE")


if __name__ == "__main__":
    unittest.main()
