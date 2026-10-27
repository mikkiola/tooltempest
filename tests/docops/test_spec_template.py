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
