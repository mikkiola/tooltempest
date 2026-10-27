"""`scripts/check_manifest.py` catches an incomplete or a stale MANIFEST.txt.

The checker compares MANIFEST.txt with `git ls-files` over the five composition
directories and exits non-zero on a mismatch in either direction. Every negative
case here runs against a disposable repository that holds a copy of the checker
and of `doc_sync.py` (which it imports); the real MANIFEST.txt is only read, to
derive realistic variants. The real checkout is checked in the positive tests.
"""
import shutil
import unittest

from dist_helpers import (COMPOSITION_DIRS, REPO_ROOT, SCRIPTS_DIR, GitFixture, TempDirCase,
                          git_out, run_python)


class ManifestFixtureCase(TempDirCase):
    def make_repo(self, extra_files=(), manifest="auto", tracked_manifest=False):
        """A repository with the checker installed, `extra_files` committed (empty), and
        a MANIFEST.txt listing every composition file ("auto"), the given text, or none."""
        fx = GitFixture()
        self.addCleanup(fx.cleanup)
        for name in ("check_manifest.py", "doc_sync.py"):
            fx.write("scripts/" + name, (SCRIPTS_DIR / name).read_bytes())
        for rel in extra_files:
            fx.write(rel, b"")
        fx.git("add", "-A")
        fx.commit("fixture")
        if manifest == "auto":
            listed = git_out("ls-files", *COMPOSITION_DIRS, cwd=fx.root).decode().split()
            manifest = "\n".join(sorted(listed)) + "\n"
        if manifest is not None:
            fx.write("MANIFEST.txt", manifest)
            if tracked_manifest:
                fx.git("add", "MANIFEST.txt")
        return fx

    def check(self, fx, cwd=None):
        return run_python([fx.root + "/scripts/check_manifest.py"], cwd=cwd or fx.root)


class NegativeCaseTests(ManifestFixtureCase):
    COMPOSITION = ("schemas/a.schema.json", "skills/demo/SKILL.md", "rules/r.md", "docs/reference/ref.md")

    def test_a_complete_manifest_passes_and_counts_the_tracked_files(self):
        fx = self.make_repo(self.COMPOSITION)
        code, out, err = self.check(fx)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("OK: MANIFEST.txt matches 6 tracked file(s)", out)  # the 4 plus the checker's own 2

    def test_a_tracked_file_missing_from_the_manifest_is_reported_by_name(self):
        fx = self.make_repo(self.COMPOSITION)
        fx.write("scripts/new_tool.py", b"")
        fx.git("add", "scripts/new_tool.py")
        code, out, err = self.check(fx)
        self.assertEqual(code, 1)
        self.assertIn("Tracked but missing from MANIFEST.txt:", err)
        self.assertIn("  scripts/new_tool.py", err)
        self.assertNotIn("OK:", out)

    def test_a_stale_entry_for_a_file_that_is_gone_is_reported_by_name(self):
        fx = self.make_repo(self.COMPOSITION)
        fx.git("rm", "-q", "-f", "rules/r.md")
        code, out, err = self.check(fx)
        self.assertEqual(code, 1)
        self.assertIn("Listed in MANIFEST.txt but not git-tracked:", err)
        self.assertIn("  rules/r.md", err)

    def test_both_kinds_of_mismatch_are_reported_together(self):
        fx = self.make_repo(self.COMPOSITION)
        fx.git("rm", "-q", "-f", "rules/r.md")
        fx.write("skills/new/SKILL.md", b"")
        fx.git("add", "skills/new/SKILL.md")
        code, out, err = self.check(fx)
        self.assertEqual(code, 1)
        self.assertIn("  skills/new/SKILL.md", err)
        self.assertIn("  rules/r.md", err)

    def test_every_composition_directory_is_checked(self):
        for directory in COMPOSITION_DIRS:
            with self.subTest(directory):
                fx = self.make_repo(self.COMPOSITION)
                rel = directory + "extra.txt"
                fx.write(rel, b"")
                fx.git("add", rel)
                code, _, err = self.check(fx)
                self.assertEqual(code, 1)
                self.assertIn("  " + rel, err)

    def test_files_outside_the_composition_directories_are_not_required(self):
        fx = self.make_repo(self.COMPOSITION + ("tests/test_x.py", "docs/adr/0001-x.md", "README.md"))
        self.assertEqual(self.check(fx)[0], 0)

    def test_a_missing_manifest_is_an_error_exit_not_a_pass(self):
        fx = self.make_repo(self.COMPOSITION, manifest=None)
        code, out, err = self.check(fx)
        self.assertEqual(code, 1)
        self.assertIn("MANIFEST.txt not found", err)
        self.assertNotIn("OK:", out)

    def test_an_empty_manifest_reports_every_tracked_file_missing(self):
        fx = self.make_repo(self.COMPOSITION, manifest="")
        code, out, err = self.check(fx)
        self.assertEqual(code, 1)
        for rel in self.COMPOSITION:
            self.assertIn("  " + rel, err)

    def test_blank_lines_and_surrounding_whitespace_in_the_manifest_are_ignored(self):
        listed = "\n".join(["", "  scripts/check_manifest.py  ", "scripts/doc_sync.py", "", ""] +
                           list(self.COMPOSITION)) + "\n"
        fx = self.make_repo(self.COMPOSITION, manifest=listed)
        self.assertEqual(self.check(fx)[0], 0)

    def test_an_untracked_composition_file_is_not_seen_by_the_check(self):
        # documented limitation (CLAUDE.md): it compares tracked paths; an untracked file still needs review
        fx = self.make_repo(self.COMPOSITION)
        fx.write("scripts/untracked.py", b"")
        code, out, _ = self.check(fx)
        self.assertEqual(code, 0, "an untracked file is outside `git ls-files`")
        fx.git("add", "scripts/untracked.py")
        self.assertEqual(self.check(fx)[0], 1, "once tracked, the same file is a mismatch")

    def test_it_works_from_a_subdirectory_of_the_repository(self):
        fx = self.make_repo(self.COMPOSITION)
        code, out, err = self.check(fx, cwd=fx.root + "/rules")
        self.assertEqual((code, err), (0, ""), out)


class VariantsOfTheRealManifestTests(ManifestFixtureCase):
    """The negative cases again, built from the real composition file set, so that the
    checker is shown to catch a damaged version of the manifest that is actually shipped."""

    def real_paths(self):
        return git_out("ls-tree", "-r", "--name-only", "HEAD", "--", *COMPOSITION_DIRS).decode().splitlines()

    def real_repo(self, manifest_lines):
        paths = [p for p in self.real_paths() if p not in ("scripts/check_manifest.py", "scripts/doc_sync.py")]
        return self.make_repo(paths, manifest="\n".join(manifest_lines) + "\n")

    def test_the_real_manifest_is_complete_for_the_real_composition(self):
        # committed state: what the candidate commit ships
        shipped = git_out("show", "HEAD:MANIFEST.txt").decode().splitlines()
        self.assertEqual(sorted(p for p in shipped if p.strip()), sorted(self.real_paths()))
        fx = self.real_repo(shipped)
        self.assertEqual(self.check(fx)[0], 0)

    def test_every_real_entry_is_needed_dropping_each_one_fails(self):
        shipped = [p for p in git_out("show", "HEAD:MANIFEST.txt").decode().splitlines() if p.strip()]
        fx = self.real_repo(shipped)
        manifest_path = fx.root + "/MANIFEST.txt"
        for dropped in shipped:
            with open(manifest_path, "w") as handle:
                handle.write("\n".join(p for p in shipped if p != dropped) + "\n")
            code, _, err = self.check(fx)
            self.assertEqual(code, 1, dropped)
            self.assertIn("Tracked but missing from MANIFEST.txt:", err, dropped)
            self.assertIn("  " + dropped, err, dropped)

    def test_a_manifest_naming_a_file_the_commit_does_not_have_is_caught(self):
        shipped = [p for p in git_out("show", "HEAD:MANIFEST.txt").decode().splitlines() if p.strip()]
        fx = self.real_repo(shipped + ["scripts/removed_in_a_later_release.py"])
        code, _, err = self.check(fx)
        self.assertEqual(code, 1)
        self.assertIn("  scripts/removed_in_a_later_release.py", err)


class RealCheckoutTests(unittest.TestCase):
    def test_the_manifest_of_this_checkout_matches_its_tracked_composition_files(self):
        code, out, err = run_python([SCRIPTS_DIR / "check_manifest.py"], cwd=REPO_ROOT)
        self.assertEqual((code, err), (0, ""), out)
        self.assertIn("OK: MANIFEST.txt matches", out)


if __name__ == "__main__":
    unittest.main()
