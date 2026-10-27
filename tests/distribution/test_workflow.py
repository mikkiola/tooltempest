"""The CI workflow that runs the manifest check and the suites (ADR-0016), checked as text.

There is no YAML parser in the standard library, so this is not a validation of the
workflow and it does not execute it: it pins what the workflow is documented to do
(its triggers and its four commands) and that every command refers to a file or a
directory that exists, so that a renamed suite or a moved script cannot leave CI
pointing at nothing. The workflow's syntax was checked with other parsers when it
was written (see ADR-0016).
"""
import re
import unittest

from dist_helpers import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "checks.yml"
EXPECTED_COMMANDS = [
    "python3 scripts/check_manifest.py",
    "python3 -B -m unittest discover -s tests/docops -t tests/docops",
    "python3 -B -m unittest discover -s tests/tooling -t tests/tooling",
    "python3 -B -m unittest discover -s tests/distribution -t tests/distribution",
]


class WorkflowTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.commands = re.findall(r"^\s+run: (.+)$", cls.text, re.MULTILINE)

    def test_it_runs_on_pull_requests_to_main_and_on_pushes_to_main(self):
        self.assertRegex(self.text, r"(?m)^on:\n  pull_request:\n    branches: \[main\]\n  push:\n    branches: \[main\]\n")

    def test_it_runs_exactly_the_documented_commands_one_per_job(self):
        self.assertEqual(self.commands, EXPECTED_COMMANDS)
        self.assertEqual(len(re.findall(r"(?m)^  [a-z]+:\n    name: ", self.text)), len(EXPECTED_COMMANDS))

    def test_every_command_refers_to_something_that_exists(self):
        for command in self.commands:
            for token in re.findall(r"(?:-s |-t |python3 )((?:scripts|tests)/[\w./-]+)", command):
                self.assertTrue((REPO_ROOT / token).exists(), "%s: %s does not exist" % (command, token))

    def test_the_commands_are_the_ones_claude_md_documents_for_local_runs(self):
        documented = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        for command in EXPECTED_COMMANDS:
            self.assertIn(command, documented)

    def test_the_python_version_is_pinned_and_the_permissions_are_read_only(self):
        self.assertEqual(set(re.findall(r"python-version: '([\d.]+)'", self.text)), {"3.14"})
        self.assertIn("permissions:\n  contents: read\n", self.text)

    def test_the_scenario_suite_workflow_of_adr_0005_is_untouched_in_its_trigger(self):
        old = (REPO_ROOT / ".github" / "workflows" / "docops-scenario-suite.yml").read_text(encoding="utf-8")
        self.assertIn("on:\n  pull_request:\n    branches: [main]\n", old)
        self.assertNotIn("push:", old)


if __name__ == "__main__":
    unittest.main()
