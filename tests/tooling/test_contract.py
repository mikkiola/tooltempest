"""The documented command-line contract of scripts/tooling.py, outcome by outcome.

The contract is stated in `tooling_core.cli`'s docstring, in ADR-0015 and in
`--help`: exit 0 (verify: local integrity and upstream provenance both
established; status: always, unless the tool fails; materialize: the plan is
executable or was applied and verified), exit 1 (something is wrong or
unestablished, or a conflict is refused), exit 2 (the tool could not run, with
an `error` object). `--format json` prints one document that conforms to
`schemas/tooling-result.schema.json`; `--format text` prints the same verdict as
text whose last line starts with `exit N`. These tests walk a table of outcomes through
every command in both formats and assert the exit code, that the exit code equals
`summary.exit_code`, schema conformance, the text/JSON parity, that a failure is
never exit 0 and a tool failure never carries a traceback. Behaviour already
covered in depth (the three properties, safety, recovery, the network) is not
repeated here.
"""
import json
import os
import re
import unittest
from unittest import mock

from tooling_helpers import (RESULT_SCHEMA, TOOLING_PY, ToolingCase, engine, load_schema, run_tooling,
                             validate)

from tooling_core import TOOL_VERSION
import tooling_core.cli as cli_module

EXIT_LINE = re.compile(r"^exit (\d)\b", re.MULTILINE)


class Outcome:
    """Expected exit codes of one scenario for each command."""

    def __init__(self, verify, status, dry, apply, error=False):
        self.verify, self.status, self.dry, self.apply, self.error = verify, status, dry, apply, error


class OutcomeTableTests(ToolingCase):
    def run_both_formats(self, command, *extra, root=None, source=None):
        base = [command, "--root", root or self.consumer.root, "--source", source or self.upstream.root, *extra]
        code_json, out_json, err_json = run_tooling([*base, "--format", "json"], self.home)
        code_text, out_text, err_text = run_tooling([*base, "--format", "text"], self.home)
        doc = json.loads(out_json)
        self.assertEqual(validate(doc, load_schema(RESULT_SCHEMA)), [], (command, extra))
        self.assertEqual(doc["summary"]["exit_code"], code_json)
        self.assertEqual(doc["command"], command)
        self.assertEqual((err_json, err_text), ("", ""), "no traceback and no stray stderr")
        # text: not JSON, ends with the same exit code
        with self.assertRaises(ValueError):
            json.loads(out_text)
        match = EXIT_LINE.findall(out_text)
        self.assertEqual(match, [str(code_text)], out_text[-200:])
        self.assertRegex(out_text.rstrip("\n").splitlines()[-1], r"^exit %d\b" % code_text)
        self.assertEqual(code_text, code_json, "the two formats must agree")
        return code_json, doc, out_text

    def assertOutcome(self, scenario, outcome):
        for command, expected in (("verify", outcome.verify), ("status", outcome.status)):
            code, doc, text = self.run_both_formats(command)
            self.assertEqual(code, expected, (scenario, command, doc["findings"]))
            if outcome.error:
                self.assertIsNotNone(doc["error"], (scenario, command))
                self.assertIn("nothing was verified or changed", text)
            else:
                self.assertIsNone(doc["error"], (scenario, command))
            if command == "verify" and expected != 0:
                self.assertNotEqual(code, 0, "a failure is never exit 0")
        code, doc, text = self.run_both_formats("materialize")
        self.assertEqual(code, outcome.dry, (scenario, "materialize dry run", doc["findings"]))
        if not outcome.error:
            self.assertTrue(doc["result"]["dry_run"])
            self.assertFalse(doc["result"]["applied"])
        if outcome.apply is not None:
            code, doc, text = self.run_both_formats("materialize", "--apply")
            self.assertEqual(code, outcome.apply, (scenario, "materialize --apply", doc["findings"]))

    # -- scenarios ------------------------------------------------------------------------
    def test_not_yet_installed_verify_fails_status_informs_materialize_plans_then_installs(self):
        self.publish_v1()
        self.assertOutcome("not installed", Outcome(verify=1, status=0, dry=0, apply=0))
        self.assertTrue(self.consumer.exists("scripts/alpha.py"))

    def test_an_established_installation_is_exit_zero_everywhere(self):
        self.publish_v1()
        self.apply()
        self.assertOutcome("installed", Outcome(verify=0, status=0, dry=0, apply=0))

    def test_a_conflict_with_an_unmanaged_file_is_exit_one_for_materialize_and_nothing_is_written(self):
        self.publish_v1()
        self.consumer.write("scripts/alpha.py", "my own file\n")
        before = self.digest(self.consumer.root)
        for fmt_args in ((), ("--apply",)):
            code, doc, _ = self.run_both_formats("materialize", *fmt_args)
            self.assertEqual(code, 1)
            self.assertIn("CONFLICT_UNMANAGED", self.codes(doc, "error"))
        self.assertEqual(self.digest(self.consumer.root), before)
        code, _, _ = self.run_both_formats("verify")
        self.assertEqual(code, 1)
        code, _, _ = self.run_both_formats("status")
        self.assertEqual(code, 0)

    def test_a_missing_lock_is_a_finding_not_a_tool_failure(self):
        self.publish_v1()
        os.unlink(self.consumer.lock_path)
        self.assertOutcome("no lock", Outcome(verify=1, status=0, dry=1, apply=1))

    def test_an_unavailable_source_leaves_provenance_unestablished_and_verify_fails(self):
        self.publish_v1()
        self.apply()
        code, doc, _ = self.run_both_formats("verify", source=os.path.join(self.home, "no-such-source"))
        self.assertEqual(code, 1)
        self.assertNotEqual(doc["summary"]["upstream_provenance"], "ok")
        code, _, _ = self.run_both_formats("status", source=os.path.join(self.home, "no-such-source"))
        self.assertEqual(code, 0)

    def test_a_root_that_is_not_a_directory_is_a_tool_failure_for_every_command(self):
        self.publish_v1()
        not_a_dir = os.path.join(self.home, "afile")
        with open(not_a_dir, "w") as handle:
            handle.write("x")
        for command, extra in (("verify", ()), ("status", ()), ("materialize", ()), ("materialize", ("--apply",))):
            with self.subTest((command, extra)):
                code, doc, text = self.run_both_formats(command, *extra, root=not_a_dir)
                self.assertEqual(code, 2)
                self.assertEqual(doc["error"]["code"], "USAGE")
                self.assertIn("nothing was verified or changed", text)
                if command == "materialize":
                    self.assertEqual(doc["result"], {"dry_run": not extra, "applied": False, "written": [],
                                                     "receipt_written": False, "post_verification": None})
                else:
                    self.assertNotIn("result", doc)

    def test_freshness_is_never_the_reason_for_a_non_zero_exit(self):
        self.publish_v1()
        self.apply()
        self.publish_v2()  # upstream moves on; the pin stays
        code, doc, text = self.run_both_formats("verify")
        self.assertEqual((code, doc["summary"]["freshness"]), (0, "behind"))
        self.assertIn("behind", text)

    def test_a_broken_installation_of_the_tool_is_the_one_exit_two_document_outside_the_schema(self):
        # tooling_core cannot be imported, so neither the version nor the command is known: the
        # fallback is a minimal JSON object with the error and exit code, by design not schema-valid
        import shutil
        import subprocess
        import sys
        import tempfile
        with tempfile.TemporaryDirectory() as lone:
            shutil.copy(str(TOOLING_PY), lone)
            proc = subprocess.run([sys.executable, "-I", os.path.join(lone, "tooling.py"), "verify"],
                                  capture_output=True, cwd=lone, stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, 2)
        doc = json.loads(proc.stdout)
        self.assertEqual((doc["error"]["code"], doc["summary"]["exit_code"], doc["tool"]["name"]),
                         ("IMPORT_FAILED", 2, "tooltempest-tooling"))
        self.assertIn("could not be imported", doc["error"]["message"])
        self.assertNotEqual(validate(doc, load_schema(RESULT_SCHEMA)), [])


class PostInstallVerificationFailureTests(ToolingCase):
    def test_a_tool_failure_after_the_files_were_installed_is_reported_as_installed_but_unverified(self):
        self.publish_v1()
        real, calls = engine.evaluate, []

        def fail_the_post_install_check(opts, **kwargs):
            calls.append(1)
            if len(calls) == 3:  # plan, re-plan under the lock, then the check after the install
                raise engine.ToolFailure("GIT_FAILED", "injected failure while verifying")
            return real(opts, **kwargs)

        with mock.patch.object(engine, "evaluate", side_effect=fail_the_post_install_check):
            doc = engine.run_materialize(self.engine_options(apply=True))
        self.assertEqual(len(calls), 3)
        self.assertEqual(validate(doc, load_schema(RESULT_SCHEMA)), [])
        self.assertEqual(doc["summary"]["exit_code"], 1)
        self.assertIsNone(doc["error"])
        self.assertEqual(self.codes(doc, "error"), ["POST_INSTALL_VERIFICATION_FAILED"])
        self.assertIn("were installed, but verifying them failed", doc["findings"][-1]["message"])
        self.assertTrue(doc["result"]["applied"])
        self.assertTrue(doc["result"]["receipt_written"])
        self.assertEqual(sorted(doc["result"]["written"]), ["schemas/beta.schema.json", "scripts/alpha.py", "scripts/run.sh"])
        self.assertEqual(doc["result"]["post_verification"],
                         {"local_integrity": "unknown", "upstream_provenance": "unknown", "ok": False})
        text = cli_module.render_text(doc)
        self.assertIn("verified afterwards: NO", text)
        self.assertNotIn("verified afterwards: yes", text)
        self.assertEqual((doc["summary"]["local_integrity"], doc["summary"]["upstream_provenance"]), ("unknown", "unknown"))
        for rel in doc["result"]["written"]:
            self.assertTrue(self.consumer.exists(rel), rel)
        self.assertEqual(self.consumer.receipt()["state"], "complete")


class SchemaGuardTests(ToolingCase):
    def test_the_schema_requires_a_result_object_for_every_materialize_document_including_a_tool_failure(self):
        code, out, _ = run_tooling(["materialize", "--root", os.path.join(self.home, "missing"), "--format", "json"],
                                   self.home)
        doc = json.loads(out)
        self.assertEqual(code, 2)
        self.assertEqual(validate(doc, load_schema(RESULT_SCHEMA)), [])
        del doc["result"]
        self.assertNotEqual(validate(doc, load_schema(RESULT_SCHEMA)), [])
        # verify and status documents have no such object
        code, out, _ = run_tooling(["verify", "--root", os.path.join(self.home, "missing"), "--format", "json"], self.home)
        self.assertEqual(validate(json.loads(out), load_schema(RESULT_SCHEMA)), [])


class HelpAndUsageTests(ToolingCase):
    def test_help_and_version_exit_zero(self):
        code, out, err = run_tooling(["--help"], self.home)
        self.assertEqual((code, err), (0, ""))
        for word in ("verify", "status", "materialize"):
            self.assertIn(word, out)
        code, out, err = run_tooling(["--version"], self.home)
        self.assertEqual((code, out.strip(), err), (0, "tooling.py " + TOOL_VERSION, ""))
        for command, options in (("verify", ("--root", "--lock", "--receipt", "--source", "--upstream-ref", "--format")),
                                 ("status", ("--root", "--lock", "--receipt", "--source", "--upstream-ref", "--format")),
                                 ("materialize", ("--apply", "--allow-network", "--root", "--source", "--format"))):
            code, out, err = run_tooling([command, "--help"], self.home)
            self.assertEqual((code, err), (0, ""), command)
            for option in options:
                self.assertIn(option, out, (command, option))

    def test_only_materialize_accepts_apply_and_network_permission(self):
        for command in ("verify", "status"):
            for flag in ("--apply", "--allow-network"):
                code, out, err = run_tooling([command, flag], self.home)
                self.assertEqual((code, out), (2, ""), (command, flag))
                self.assertIn("unrecognized arguments", err)

    def test_usage_errors_exit_two_on_stderr_without_a_verdict(self):
        for args in ([], ["bogus"], ["verify", "--format", "xml"], ["materialize", "--bogus"]):
            with self.subTest(args):
                code, out, err = run_tooling(args, self.home)
                self.assertEqual((code, out), (2, ""))
                self.assertIn("error", err.lower())

    def test_dry_run_is_the_default_and_apply_is_explicit(self):
        parser = cli_module._parser()
        self.assertFalse(parser.parse_args(["materialize"]).apply)
        self.assertFalse(parser.parse_args(["materialize"]).allow_network)
        self.assertTrue(parser.parse_args(["materialize", "--apply"]).apply)
        self.assertEqual(parser.parse_args(["verify"]).format, "text")


class DocumentedCodesTests(ToolingCase):
    def test_the_help_text_lists_exactly_the_codes_the_schema_allows(self):
        allowed = set(load_schema(RESULT_SCHEMA)["properties"]["summary"]["properties"]["exit_code"]["enum"])
        for args in (["--help"], ["verify", "--help"], ["status", "--help"], ["materialize", "--help"]):
            code, out, err = run_tooling(args, self.home)
            self.assertEqual((code, err), (0, ""), args)
            self.assertIn("exit codes:", out)
            listed = {int(m.group(1)) for m in re.finditer(r"^\s{2}(\d)\s{2}\S", out.split("exit codes:", 1)[1], re.MULTILINE)}
            self.assertEqual(listed, allowed, args)

    def test_the_cli_docstring_lists_exactly_the_codes_the_schema_allows(self):
        documented = {int(m.group(1)) for m in re.finditer(r"^\s{2}(\d)\s{2}\S", cli_module.__doc__, re.MULTILINE)}
        allowed = set(load_schema(RESULT_SCHEMA)["properties"]["summary"]["properties"]["exit_code"]["enum"])
        self.assertEqual(documented, allowed)
        self.assertEqual(allowed, {0, 1, 2})


if __name__ == "__main__":
    unittest.main()
