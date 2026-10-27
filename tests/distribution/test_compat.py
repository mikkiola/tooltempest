"""The import surface the legacy scripts share (the real compatibility surface).

`scripts/doc_sync_tier2.py` imports `relative_to_root`, `repo_root` and
`restore_snapshots` from `scripts/doc_sync.py`; `scripts/check_manifest.py`
imports `repo_root` from it. Consumers copy these files together, so a rename
or a change of behaviour of those three functions breaks them. These tests pin
that the names stay importable from the places consumers import them, that the
re-exports are the same objects, and that each function keeps the behaviour its
callers rely on.
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

from dist_helpers import REPO_ROOT, SCRIPTS_DIR, GitFixture, TempDirCase, clean_env, run_python  # noqa: F401

import doc_sync  # noqa: E402  (scripts/ is on sys.path via dist_helpers)
import doc_sync_tier2  # noqa: E402


class ImportSurfaceTests(TempDirCase):
    def test_tier2_imports_the_three_names_from_doc_sync_and_reexports_the_same_objects(self):
        code = (
            "import sys; sys.path.insert(0, %r); import doc_sync, doc_sync_tier2 as t; "
            "assert t.relative_to_root is doc_sync.relative_to_root; "
            "assert t.repo_root is doc_sync.repo_root; "
            "assert t.restore_snapshots is doc_sync.restore_snapshots; "
            "assert 'restore_snapshots' in t.__all__; print('ok')" % str(SCRIPTS_DIR))
        exit_code, out, err = run_python(["-c", code], cwd=self.tempdir())
        self.assertEqual((exit_code, out.strip()), (0, "ok"), err)

    def test_the_names_are_importable_without_the_scripts_directory_being_the_working_directory(self):
        code = "import sys; sys.path.insert(0, %r); from doc_sync import relative_to_root, repo_root, restore_snapshots" % str(SCRIPTS_DIR)
        self.assertEqual(run_python(["-c", code], cwd=self.tempdir())[0], 0)

    def test_tier2_starts_by_path_from_another_directory(self):
        # it adds its own directory to sys.path before importing doc_sync
        exit_code, out, err = run_python([SCRIPTS_DIR / "doc_sync_tier2.py", "--help"], cwd=self.tempdir())
        self.assertEqual(exit_code, 0, err)
        self.assertIn("usage", out.lower())

    def test_the_documented_signatures_have_not_changed(self):
        import inspect
        self.assertEqual(list(inspect.signature(doc_sync.repo_root).parameters), [])
        self.assertEqual(list(inspect.signature(doc_sync.relative_to_root).parameters), ["root", "absolute_path"])
        self.assertEqual(list(inspect.signature(doc_sync.restore_snapshots).parameters),
                         ["root", "snapshots", "modified"])


class RepoRootTests(TempDirCase):
    def root_from(self, cwd):
        code = "import sys; sys.path.insert(0, %r); import doc_sync; print(doc_sync.repo_root())" % str(SCRIPTS_DIR)
        return run_python(["-c", code], cwd=cwd)

    def test_the_top_level_is_found_from_the_root_a_subdirectory_and_a_linked_worktree(self):
        with GitFixture() as fx:
            fx.commit_files({"a/b/file.txt": "x\n"})
            self.assertEqual(self.root_from(fx.root)[1].strip(), fx.root)
            self.assertEqual(self.root_from(os.path.join(fx.root, "a", "b"))[1].strip(), fx.root)
            linked = str(self.tempdir() / "linked")
            fx.git("worktree", "add", "-q", "-b", "side", linked)
            self.assertEqual(self.root_from(linked)[1].strip(), os.path.realpath(linked))

    def test_outside_a_repository_it_fails_instead_of_guessing_a_root(self):
        exit_code, out, err = self.root_from(self.tempdir())
        self.assertNotEqual(exit_code, 0)
        self.assertEqual(out.strip(), "")
        self.assertIn("CalledProcessError", err)


class RelativeToRootTests(TempDirCase):
    def test_a_path_inside_the_root_becomes_a_relative_string(self):
        root = self.tempdir()
        (root / "docs").mkdir()
        self.assertEqual(doc_sync.relative_to_root(root, str(root / "docs" / "ARCHITECTURE.md")),
                         "docs/ARCHITECTURE.md")
        self.assertIsInstance(doc_sync.relative_to_root(root, str(root / "README.md")), str)

    def test_dot_dot_components_are_resolved_before_the_comparison(self):
        root = self.tempdir()
        (root / "docs").mkdir()
        self.assertEqual(doc_sync.relative_to_root(root, str(root / "docs" / ".." / "README.md")), "README.md")

    def test_a_path_outside_the_root_is_an_error_not_a_relative_path_with_dots(self):
        root = self.tempdir() / "inside"
        root.mkdir()
        with self.assertRaises(ValueError):
            doc_sync.relative_to_root(root, str(root.parent / "elsewhere.md"))

    def test_a_symlink_that_leaves_the_root_is_judged_by_where_it_points(self):
        base = self.tempdir()
        root = base / "root"
        root.mkdir()
        (base / "outside.md").write_text("x")
        (root / "link.md").symlink_to(base / "outside.md")
        with self.assertRaises(ValueError):
            doc_sync.relative_to_root(root, str(root / "link.md"))


class RestoreSnapshotsTests(TempDirCase):
    def test_a_modified_file_gets_its_exact_original_bytes_back(self):
        root = self.tempdir()
        (root / "README.md").write_text("original\nwith an unstaged human edit\n", encoding="utf-8")
        snapshots = {"README.md": (True, "original\nwith an unstaged human edit\n")}
        (root / "README.md").write_text("rewritten by a tool\n", encoding="utf-8")
        doc_sync.restore_snapshots(root, snapshots, ["README.md"])
        self.assertEqual((root / "README.md").read_text(encoding="utf-8"), "original\nwith an unstaged human edit\n")

    def test_a_file_that_did_not_exist_before_is_deleted_again(self):
        root = self.tempdir()
        (root / "docs").mkdir()
        (root / "docs" / "ROADMAP.md").write_text("created by the tool\n")
        doc_sync.restore_snapshots(root, {"docs/ROADMAP.md": (False, None)}, ["docs/ROADMAP.md"])
        self.assertFalse((root / "docs" / "ROADMAP.md").exists())

    def test_a_file_that_did_not_exist_and_still_does_not_is_left_alone_without_error(self):
        root = self.tempdir()
        doc_sync.restore_snapshots(root, {"docs/ROADMAP.md": (False, None)}, ["docs/ROADMAP.md"])
        self.assertEqual(list(root.iterdir()), [])

    def test_only_the_listed_files_are_touched(self):
        root = self.tempdir()
        (root / "a.md").write_text("changed a\n")
        (root / "b.md").write_text("changed b\n")
        snapshots = {"a.md": (True, "old a\n"), "b.md": (True, "old b\n")}
        doc_sync.restore_snapshots(root, snapshots, ["a.md"])
        self.assertEqual((root / "a.md").read_text(), "old a\n")
        self.assertEqual((root / "b.md").read_text(), "changed b\n")

    def test_it_restores_from_git_checkout_nothing_the_committed_version_is_not_consulted(self):
        with GitFixture() as fx:
            fx.commit_files({"README.md": "committed\n"})
            fx.write("README.md", "unstaged human edit\n")
            snapshots = {"README.md": (True, "unstaged human edit\n")}
            fx.write("README.md", "tool output\n")
            doc_sync.restore_snapshots(Path(fx.root), snapshots, ["README.md"])
            self.assertEqual(Path(fx.root, "README.md").read_text(), "unstaged human edit\n")


class Tier2RoundTripTests(TempDirCase):
    def test_a_snapshot_taken_by_tier2_is_restorable_by_the_shared_primitive(self):
        root = self.tempdir()
        (root / "docs").mkdir()
        (root / "README.md").write_text("readme before\n", encoding="utf-8")
        (root / "docs" / "ARCHITECTURE.md").write_text("arch before\n", encoding="utf-8")
        snapshots = doc_sync_tier2.snapshot_tier2_docs(root)
        self.assertEqual(sorted(snapshots), sorted(doc_sync_tier2.TIER2_DOCS))
        self.assertEqual(snapshots["README.md"], (True, "readme before\n"))
        self.assertEqual(snapshots["docs/BACKLOG.md"], (False, None))
        (root / "README.md").write_text("tool rewrite\n", encoding="utf-8")
        (root / "docs" / "BACKLOG.md").write_text("new file\n", encoding="utf-8")
        doc_sync.restore_snapshots(root, snapshots, ["README.md", "docs/BACKLOG.md"])
        self.assertEqual((root / "README.md").read_text(encoding="utf-8"), "readme before\n")
        self.assertFalse((root / "docs" / "BACKLOG.md").exists())
        self.assertEqual((root / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8"), "arch before\n")


if __name__ == "__main__":
    unittest.main()
