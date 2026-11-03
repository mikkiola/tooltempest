"""The SPEC formats that ToolTempest's own canonical sources prescribe, judged by the checker.

`skills/spec/SKILL.md` (Step 4) tells an author what to write into SPEC.md, and
`rules/drift-control.md` shows how milestone fields are laid out. Both are read
as SPEC content by `docops check` (ADR-0013 deny-list grammar), so an example
the checker rejects sends authors into a failing commit. These tests extract the
fenced example from each canonical file (no copy of it lives here), commit it
into a throwaway Git repository and run the production checker on it. They do
not show that the surrounding prose is correct, only that the prescribed
format is accepted.
"""
import re
import unittest
from pathlib import Path

from helpers import REPO_ROOT, DocopsTestCase, GitFixture, run_check_json

SPEC_SKILL = Path(REPO_ROOT) / "skills" / "spec" / "SKILL.md"
DRIFT_CONTROL = Path(REPO_ROOT) / "rules" / "drift-control.md"

# A fence opened and closed at column 0, as the checker itself requires.
MARKDOWN_BLOCK = re.compile(r"^```markdown\n(.*?)^```$", re.DOTALL | re.MULTILINE)


def sole_markdown_block(text, label):
    """The one ```markdown block of `text`; AssertionError if there is none or several."""
    blocks = [match.group(1) for match in MARKDOWN_BLOCK.finditer(text)]
    if len(blocks) != 1:
        raise AssertionError("expected exactly one ```markdown block in %s, found %d" % (label, len(blocks)))
    if not blocks[0].strip():
        raise AssertionError("the ```markdown block in %s is empty" % label)
    return blocks[0]


def canonical_block(path):
    return sole_markdown_block(path.read_text(encoding="utf-8"), str(path.relative_to(REPO_ROOT)))


class SpecSkillLocationTests(unittest.TestCase):
    """The /spec skill is an instruction to a model, so what it makes the model do cannot be
    tested here. These tests pin only the instruction text: the skill must tell the model to
    resolve the repository root from the Git top level, to write or update the root SPEC.md
    only, and never to create a nested or second SPEC.md, and must not tell it to use the
    current directory. They cannot show that a model follows the text."""

    def setUp(self):
        self.text = SPEC_SKILL.read_text(encoding="utf-8")
        self.frontmatter, body = self.text.split("\n---\n", 1)
        self.body = " ".join(body.split())  # line wrapping is not part of the instruction

    def test_the_skill_resolves_the_root_from_the_git_top_level_not_the_current_directory(self):
        self.assertIn("git rev-parse --show-toplevel", self.body)
        self.assertIn("not from the current directory", self.body)
        self.assertNotIn("exists in the current directory", self.body)

    def test_the_skill_writes_only_the_root_spec_and_never_a_second_one(self):
        self.assertIn("`SPEC.md` at the repository root", self.body)
        self.assertIn("Never create a nested or second `SPEC.md`", self.body)
        self.assertIn(".docops-policy.json", self.body)
        self.assertIn("root `SPEC.md` of Step 1", self.body)  # Step 4 writes to the Step 1 file

    def test_the_only_shell_permission_is_the_git_root_lookup(self):
        line = next(l for l in self.frontmatter.splitlines() if l.startswith("allowed-tools:"))
        tools = [t.strip() for t in line.split(":", 1)[1].split(",")]
        self.assertEqual(tools, ["Read", "Write", "Edit", "Bash(git rev-parse --show-toplevel)"])


class DocSyncSkillTests(unittest.TestCase):
    """The doc-sync skill must describe the shared checker as the current mechanism and the
    legacy hooks only as a legacy path. Text-level only: the tests cannot show that the
    description is true of a given consumer's hooks."""

    def setUp(self):
        text = (Path(REPO_ROOT) / "skills" / "doc-sync" / "SKILL.md").read_text(encoding="utf-8")
        self.text = " ".join(text.split())

    def test_it_describes_the_shared_checker_its_exit_codes_and_the_unregistered_spec_rule(self):
        for fragment in ("scripts/docops.py check --tree index", "scripts/docops.py check --push-stdin",
                         "OUT_OF_SCOPE_SPEC", "Any other `SPEC.md` is a FAIL (exit 1)",
                         "The root `SPEC.md` is always evaluated", "non_active_specs",
                         "| 0 |", "| 1 |", "| 2 |", "| 3 |"):
            self.assertIn(fragment, self.text)

    def test_it_states_the_absent_root_rule_with_its_exception_and_the_extra_fail_cases(self):
        for fragment in ("is absent and no policy entry names it", "`SPEC_MISSING`",
                         "`SPEC_IS_DIRECTORY`", "a track named `root` at another path"):
            self.assertIn(fragment, self.text)
        self.assertNotIn("If the root `SPEC.md` is absent, there is no SPEC", self.text)

    def test_it_says_the_checker_writes_no_run_record(self):
        self.assertIn("writes no run record", self.text)
        self.assertIn("no `.tempest/runs/` record", self.text)

    def test_the_legacy_hooks_are_described_as_a_legacy_path_not_as_the_active_one(self):
        self.assertIn("legacy path (consumers whose hooks still call `doc_sync.py`)".lower(), self.text.lower())
        self.assertIn("applies only to a project whose own hooks still run `scripts/doc_sync.py`", self.text)
        self.assertNotIn("runs automatically from a consuming project's own", self.text)


class SpecFormatTests(DocopsTestCase):
    def check_spec(self, content):
        """Commit `content` as SPEC.md and return (exit code, SPEC_STRUCTURE check)."""
        with GitFixture() as fx:
            fx.commit_files({"SPEC.md": content})
            code, result, _ = run_check_json(["--repo", fx.root, "--tree", "HEAD"])
        self.assertValidResult(result)
        return code, result["snapshots"][0]["checks"]["SPEC_STRUCTURE"]

    def assertSpecPasses(self, content, milestones_total):
        code, check = self.check_spec(content)
        message = "the checker rejected the canonical format: %r" % (check,)
        self.assertEqual(check["status"], "PASS", message)
        self.assertEqual(code, 0, message)
        tracks = check["tracks"]
        self.assertEqual(len(tracks), 1, message)
        self.assertEqual(tracks[0]["milestones"]["total"], milestones_total, message)

    def test_the_spec_skill_template_passes_the_checker(self):
        template = canonical_block(SPEC_SKILL)
        self.assertIn("## Milestones", template, "the /spec template lost its Milestones section")
        self.assertSpecPasses(template, milestones_total=2)

    def test_the_drift_control_example_passes_the_checker_as_a_spec(self):
        example = canonical_block(DRIFT_CONTROL)
        self.assertIn("### M1:", example, "the drift-control example lost its milestone heading")
        self.assertIn("  drift: goal 0.0, constraint 0.0, scope 0.0, combined 0.0", example.splitlines(),
                      "the drift-control example lost its two-space-indented single-key drift: line")
        for axis in ("goal", "constraint", "scope", "combined"):
            self.assertNotIn("drift-%s:" % axis, example, "the example uses a separate drift-%s: key" % axis)
        spec = "# Drift example\n\n## Milestones\n\n" + example
        self.assertSpecPasses(spec, milestones_total=1)

    def test_a_missing_duplicated_or_empty_block_is_an_error_not_a_pass(self):
        for text in ("no fences here\n", "```markdown\na\n```\n\n```markdown\nb\n```\n", "```markdown\n\n```\n"):
            with self.assertRaises(AssertionError, msg=text):
                sole_markdown_block(text, "fixture")
        self.assertEqual(sole_markdown_block("```markdown\nx\n```\n", "fixture"), "x\n")

    def test_the_checker_rejects_the_formats_the_canonical_files_used_to_prescribe(self):
        legacy = {
            "DENIED_HEADING_MARKUP": "# [Feature Name] Specification\n\n## Milestones\n\n- [ ] Phase 1\n",
            "DENIED_ORDERED_LIST": "# Feature\n\n## Milestones\n\n1. [ ] Phase 1\n",
            "DENIED_NESTED_LIST": "# Feature\n\n## Milestones\n\n- [ ] M1\n- drift:\n  - goal: 0.0\n",
        }
        for code, content in legacy.items():
            check = self.check_spec(content)[1]
            self.assertEqual(check["status"], "FAIL", code)
            self.assertIn(code, repr(check), code)


if __name__ == "__main__":
    unittest.main()
