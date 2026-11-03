"""The documented command-line contract of scripts/docops.py, outcome by outcome.

The contract is stated in `docops_core.cli`'s docstring, in `scripts/docops.py`'s
docstring and in `--help`: exit 0 (every evaluated check PASS or N/A), 1 (an
invariant is violated, or a supplied impact report's required assessment is not
complete), 2 (the checker could not produce a verdict: reported through the exit
code and an `error` object), 3 (SPEC applicability cannot be established); `check`
prints the versioned JSON result, `report` the same result as text. These tests
walk one table of outcomes through both commands and assert, for each, the exit
code, that the exit code equals `summary.exit_code`, that `check` prints exactly
one JSON document that conforms to `schemas/docops-result.schema.json`, that
`report` prints text that carries the same verdict and exit code, and that no
failure path exits 0. They also pin that the documentation and the schema agree
on the set of exit codes. Cases already covered in depth elsewhere (push stdin
details, impact validation rules, parser rules) are not repeated.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

from helpers import (DOCOPS_PY, GOOD_SPEC, IDLE_SPEC, RESULT_SCHEMA, SCRIPTS_DIR, DocopsTestCase,
                     GitFixture, clean_env, load_result_schema, run_cli)

import docops_core.cli as cli_module

BAD_SPEC = "# Broken\n\n## Goals\n\n- [ ] only a goal\n"
ZERO = "0" * 40
# Exit 3 stays part of the contract, but since ADR-0013 Decision 7 was amended no SPEC scope rule
# produces a SKIP of SPEC_STRUCTURE, so no repository state reaches it. The exit-3 rows run the real
# CLI with the SPEC check's status replaced by SKIP (everything else is the real code path): this
# proves the exit code, the aggregation, the schema conformance and the rendering for SKIP, and
# nothing about when a SKIP would arise.
SKIP_WRAPPER = """
import os, sys
sys.dont_write_bytecode = True
sys.path.insert(0, %r)
from docops_core import checks, cli
_real = checks.check_spec_structure
def _skip(snapshot):
    result = _real(snapshot)
    if snapshot.identity.get("commit") == os.environ["DOCOPS_TEST_SKIP_COMMIT"]:
        result.update(status="SKIP", reason="synthetic SKIP for the exit-3 contract test")
    return result
checks.check_spec_structure = _skip
sys.exit(cli.main(sys.argv[1:]))
""" % str(SCRIPTS_DIR)
SUMMARY_LINE = re.compile(r"^summary: (?P<status>.+?) \(exit (?P<code>\d)\)", re.MULTILINE)


class OutcomeTable(DocopsTestCase):
    """One repository with one commit per SPEC outcome, and temporary impact-report files."""

    @classmethod
    def setUpClass(cls):
        cls.fx = GitFixture()
        fx = cls.fx
        fx.commit_files({"README.md": "x\n"})
        cls.commits = {
            "N/A": fx.commit_files({"SPEC.md": IDLE_SPEC}, "idle"),
            "PASS": fx.commit_files({"SPEC.md": GOOD_SPEC}, "good"),
            "FAIL": fx.commit_files({"SPEC.md": BAD_SPEC}, "bad"),
        }
        # a valid SPEC in its own commit; SKIP_WRAPPER turns the verdict of this commit only into SKIP
        cls.commits["SKIP"] = fx.commit_files({"SPEC.md": GOOD_SPEC, "NOTE.md": "skip target\n"}, "skip target")
        cls.tmp = tempfile.mkdtemp(prefix="docops-contract-")
        cls.reports = {}
        for name, text in (("malformed", "{not json"), ("empty-object", "{}")):
            path = os.path.join(cls.tmp, name + ".json")
            with open(path, "w") as handle:
                handle.write(text)
            cls.reports[name] = path
        cls.reports["missing"] = os.path.join(cls.tmp, "does-not-exist.json")

    @classmethod
    def tearDownClass(cls):
        import shutil
        cls.fx.cleanup()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # -- running both commands on the same arguments -------------------------------------
    def run_synthetic_skip(self, args, stdin=b""):
        env = clean_env({"DOCOPS_TEST_SKIP_COMMIT": self.commits["SKIP"]})
        proc = subprocess.run([sys.executable, "-I", "-c", SKIP_WRAPPER, *args], input=stdin,
                              capture_output=True, env=env, check=False)
        return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")

    def both(self, args, stdin=b"", skip=False):
        root = self.fx.root
        run = self.run_synthetic_skip if skip else run_cli
        code_check, out_check, err_check = run(["check", "--repo", root, *args], stdin=stdin)
        code_report, out_report, err_report = run(["report", "--repo", root, *args], stdin=stdin)
        return (code_check, out_check, err_check), (code_report, out_report, err_report)

    def assertContract(self, args, expected_exit, expected_status, stdin=b"", skip=False):
        (code, out, err), (rcode, rout, rerr) = self.both(args, stdin, skip)
        # check: one JSON document, schema-valid, exit code = summary.exit_code
        result = json.loads(out)
        self.assertValidResult(result)
        self.assertEqual(code, expected_exit, (args, result["summary"]))
        self.assertEqual(result["summary"]["exit_code"], expected_exit)
        self.assertEqual(result["summary"]["status"], expected_status)
        self.assertEqual(result["command"], "check")
        self.assertEqual(err, "")
        # report: text, same verdict and exit code, never JSON
        self.assertEqual(rcode, expected_exit, (args, rout[-300:]))
        self.assertEqual(rerr, "")
        with self.assertRaises(ValueError):
            json.loads(rout)
        match = SUMMARY_LINE.search(rout)
        self.assertIsNotNone(match, rout[-300:])
        self.assertRegex(rout.rstrip("\n").splitlines()[-1], r"^summary: ")
        self.assertEqual(int(match.group("code")), expected_exit)
        self.assertEqual(match.group("status"), expected_status if expected_status else "NO VERDICT (tool error)")
        # no failure path exits 0
        if expected_status in ("FAIL", "SKIP", None):
            self.assertNotEqual(code, 0)
            self.assertNotEqual(rcode, 0)
        return result

    # -- the table --------------------------------------------------------------------------
    def test_pass_exits_zero(self):
        self.assertContract(["--tree", self.commits["PASS"]], 0, "PASS")

    def test_not_applicable_exits_zero_and_is_not_reported_as_pass(self):
        result = self.assertContract(["--tree", self.commits["N/A"]], 0, "N/A")
        self.assertNotEqual(result["snapshots"][0]["status"], "PASS")

    def test_fail_exits_one(self):
        self.assertContract(["--tree", self.commits["FAIL"]], 1, "FAIL")

    def test_applicability_not_established_exits_three(self):
        result = self.assertContract(["--tree", self.commits["SKIP"]], 3, "SKIP", skip=True)
        self.assertEqual(result["snapshots"][0]["checks"]["SPEC_STRUCTURE"]["status"], "SKIP")

    def test_no_repository_state_reaches_exit_three_through_the_real_code_path(self):
        # an unclassified SPEC.md fails (exit 1); it is never SKIP, never exit 3
        with GitFixture() as fx:
            fx.commit_files({"SPEC.md": IDLE_SPEC, "deep/er/SPEC.md": GOOD_SPEC})
            code, out, _ = run_cli(["check", "--repo", fx.root, "--tree", "HEAD"])
            self.assertEqual((code, json.loads(out)["summary"]["status"]), (1, "FAIL"))

    def test_a_snapshot_that_cannot_be_read_is_a_tool_error_exit_two_with_an_error_object(self):
        result = self.assertContract(["--tree", "no-such-ref"], 2, None)
        self.assertIsNotNone(result["snapshots"][0]["error"])
        self.assertIsNone(result["snapshots"][0]["status"])

    def test_a_malformed_impact_report_is_a_fail_exit_one(self):
        result = self.assertContract(["--tree", self.commits["PASS"], "--impact-file", self.reports["malformed"]],
                                     1, "FAIL")
        self.assertEqual(result["snapshots"][0]["checks"]["DOC_IMPACT_ASSESSMENT"]["status"], "FAIL")

    def test_an_incomplete_impact_report_is_exit_one_and_never_exit_zero(self):
        result = self.assertContract(["--tree", self.commits["PASS"], "--impact-file", self.reports["empty-object"]],
                                     1, "FAIL")
        self.assertNotEqual(result["snapshots"][0]["checks"]["DOC_IMPACT_ASSESSMENT"]["status"], "PASS")

    def test_an_unreadable_impact_report_is_a_tool_error_exit_two_not_a_verdict(self):
        self.assertContract(["--tree", self.commits["PASS"], "--impact-file", self.reports["missing"]], 2, None)

    def test_the_summary_exit_code_is_the_worst_of_the_snapshots(self):
        pass_, fail, skip = (self.commits[k] for k in ("PASS", "FAIL", "SKIP"))
        self.assertContract(["--tree", pass_, "--tree", fail], 1, "FAIL")
        self.assertContract(["--tree", pass_, "--tree", skip], 3, "SKIP", skip=True)
        self.assertContract(["--tree", skip, "--tree", fail], 1, "FAIL", skip=True)  # 1 over 3
        (code, out, _), _ = self.both(["--tree", fail, "--tree", "no-such-ref"])
        self.assertEqual((code, json.loads(out)["summary"]["exit_code"]), (2, 2))  # a tool error wins

    def test_push_stdin_obeys_the_same_table(self):
        good = "refs/heads/main %s refs/heads/main %s\n" % (self.commits["PASS"], ZERO)
        bad = "refs/heads/bad %s refs/heads/bad %s\n" % (self.commits["FAIL"], ZERO)
        deleted = "(delete) %s refs/heads/gone %s\n" % (ZERO, self.commits["PASS"])
        self.assertContract(["--push-stdin"], 0, "PASS", stdin=good.encode())
        self.assertContract(["--push-stdin"], 1, "FAIL", stdin=(good + bad).encode())
        self.assertContract(["--push-stdin"], 0, "N/A", stdin=deleted.encode())
        self.assertContract(["--push-stdin"], 2, None, stdin=b"")  # no ref updates: a usage-level tool error

    def test_not_a_repository_and_a_missing_path_are_tool_errors_in_both_commands(self):
        with tempfile.TemporaryDirectory() as plain:
            for repo in (plain, os.path.join(plain, "missing")):
                for command in ("check", "report"):
                    code, out, err = run_cli([command, "--repo", repo, "--tree", "index"])
                    self.assertEqual(code, 2, (repo, command))
                    if command == "check":
                        result = json.loads(out)
                        self.assertValidResult(result)
                        self.assertEqual((result["summary"]["status"], result["summary"]["exit_code"]), (None, 2))
                    else:
                        self.assertIn("NO VERDICT (tool error)", out)


class UsageAndHelpTests(DocopsTestCase):
    def test_help_exits_zero_and_names_every_command_and_option(self):
        code, out, err = run_cli(["--help"])
        self.assertEqual((code, err), (0, ""))
        for word in ("check", "report"):
            self.assertIn(word, out)
        for command in ("check", "report"):
            code, out, err = run_cli([command, "--help"])
            self.assertEqual((code, err), (0, ""), command)
            for option in ("--tree", "--push-stdin", "--impact-file", "--repo"):
                self.assertIn(option, out, (command, option))

    def test_usage_errors_exit_two_print_to_stderr_and_never_print_a_verdict(self):
        cases = [[], ["bogus"], ["check"], ["report"], ["check", "--bogus"], ["check", "--tree"],
                 ["check", "--tree", "index", "--impact-file", "-", "--push-stdin"]]
        for args in cases:
            with self.subTest(args):
                code, out, err = run_cli(args)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn("error", err.lower(), "a usage error explains itself on stderr")


class DocumentedCodesTests(unittest.TestCase):
    """The documentation, the schema and the code state one set of exit codes."""

    def documented(self, text):
        return {int(m.group(1)) for m in re.finditer(r"^\s{2}(\d)\s{2}\S", text, re.MULTILINE)}

    def test_the_cli_docstring_lists_exactly_the_codes_the_schema_allows(self):
        schema = load_result_schema()
        allowed = set(schema["properties"]["summary"]["properties"]["exit_code"]["enum"])
        self.assertEqual(self.documented(cli_module.__doc__), allowed)
        self.assertEqual(allowed, {0, 1, 2, 3})

    def test_the_help_text_lists_every_code_and_the_same_set_as_the_schema(self):
        allowed = set(load_result_schema()["properties"]["summary"]["properties"]["exit_code"]["enum"])
        for args in (["--help"], ["check", "--help"], ["report", "--help"]):
            code, out, err = run_cli(args)
            self.assertEqual((code, err), (0, ""), args)
            self.assertIn("exit codes:", out)
            self.assertEqual(self.documented(out.split("exit codes:", 1)[1]), allowed, args)

    def test_the_script_docstring_mentions_every_code(self):
        source = DOCOPS_PY.read_text(encoding="utf-8")
        docstring = source.split('"""')[1]
        for fragment in ("Exit 0", "1 on a", "2 when", "3 when"):
            self.assertIn(fragment, docstring)


if __name__ == "__main__":
    unittest.main()
