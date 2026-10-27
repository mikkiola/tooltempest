"""The SPEC fixtures of the legacy Tier 1 suite, judged by the production parser.

`tests/test-doc-sync.sh` drives `scripts/doc_sync.py` with a stub `verify.py`
(`tests/fixtures/verify.py`) that implements only the consumer-side output
contract Tier 1 reads. The stub is not the production parser, so nothing in
that suite shows that its three SPEC fixtures are what they claim to be. Here
the production parser (`docops_core.specparse`) judges the same files and the
stub classifies them, and the two must agree on the class of each fixture:
well-formed, no milestones section, malformed milestone. The suite does not
show that Tier 1 works (that is `tests/test-doc-sync.sh`).
"""
import importlib.util
import unittest
from pathlib import Path

from helpers import DocopsTestCase, REPO_ROOT  # noqa: F401  (sys.path setup)

from docops_core.specparse import FAIL, PASS, analyze_spec

FIXTURES = Path(REPO_ROOT) / "tests" / "fixtures"


def load_stub():
    spec = importlib.util.spec_from_file_location("tier1_verify_stub", FIXTURES / "verify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read(name):
    return (FIXTURES / name).read_bytes()


class Tier1FixtureTests(DocopsTestCase):
    @classmethod
    def setUpClass(cls):
        cls.stub = load_stub()

    def judge(self, name):
        verdict = analyze_spec(read(name))
        pattern, structure = self.stub.classify(read(name).decode("utf-8"))
        return verdict, pattern, structure

    def test_the_well_formed_fixture_passes_in_both(self):
        verdict, pattern, structure = self.judge("SPEC.md")
        self.assertEqual((verdict.status, verdict.state), (PASS, "active"))
        self.assertEqual(verdict.milestones, {"total": 2, "checked": 1, "unchecked": 1})
        self.assertEqual((pattern, structure["status"]), ("inline_spec", "OK"))
        self.assertEqual((structure["total_units"], structure["well_formed"]), (2, 2))

    def test_the_fixture_without_a_milestones_section_fails_in_the_checker_and_is_unknown_to_the_stub(self):
        verdict, pattern, structure = self.judge("SPEC-no-milestones.md")
        self.assertEqual(verdict.status, FAIL)
        self.assertEqual([d.code for d in verdict.diagnostics], ["MILESTONES_MISSING"])
        self.assertEqual((pattern, structure["status"]), ("UNKNOWN", "UNKNOWN"))

    def test_the_fixture_with_an_empty_milestone_fails_in_the_checker_and_is_malformed_to_the_stub(self):
        verdict, pattern, structure = self.judge("SPEC-malformed.md")
        self.assertEqual(verdict.status, FAIL)
        self.assertEqual([d.code for d in verdict.diagnostics], ["CHECKBOX_EMPTY"])
        self.assertEqual((pattern, structure["status"]), ("inline_spec", "MALFORMED"))
        self.assertEqual((structure["total_units"], structure["well_formed"]), (2, 1))
        self.assertEqual([d["line"] for d in structure["malformed_details"]], [10])

    def test_the_retired_checkpoint_pairing_is_gone_from_the_fixtures(self):
        self.assertFalse((FIXTURES / "CHECKPOINT.md").exists())
        for name in ("SPEC.md", "SPEC-no-milestones.md", "SPEC-malformed.md"):
            self.assertNotIn("CHECKPOINT", read(name).decode("utf-8"), name)


if __name__ == "__main__":
    unittest.main()
