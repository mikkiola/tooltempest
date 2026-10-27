"""The descriptor-anchored primitives (scripts/tooling_core/fsops.py) on their own.

Each test builds a throwaway directory, performs a swap the way a concurrent actor would,
and checks that the primitive acts on what it opened, never on what the path now names.
"""
import errno
import os
import shutil
import stat
import tempfile
import unittest
from unittest import mock

from tooling_helpers import docops_helpers  # noqa: F401  (sets up sys.path like the other modules)
from tooling_core import fsops


def snap(path):
    with open(path, "rb") as handle:
        return handle.read(), stat.S_IMODE(os.lstat(path).st_mode)


class FsopsCase(unittest.TestCase):
    def setUp(self):
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="fsops-"))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root = os.path.join(self.base, "root")
        self.outside = os.path.join(self.base, "outside")
        os.mkdir(self.root)
        os.mkdir(self.outside)
        self.tree = fsops.Tree(self.root)
        self.addCleanup(self.tree.close)

    def user_file(self, path, mode=0o640):
        with open(path, "wb") as handle:
            handle.write(b"user bytes\n")
        os.chmod(path, mode)
        return snap(path)


class SupportTests(FsopsCase):
    def test_support_is_reported_when_a_primitive_is_missing(self):
        fsops.require_support()  # this platform has them
        for removed in (os.open, os.mkdir, os.unlink, os.rmdir, os.stat, os.rename):
            with self.subTest(removed.__name__):
                with mock.patch.object(fsops.os, "supports_dir_fd", set(os.supports_dir_fd) - {removed}):
                    with self.assertRaises(fsops.Unsupported) as ctx:
                        fsops.require_support()
                    self.assertIn(removed.__name__, str(ctx.exception))
        with mock.patch.object(fsops.os, "supports_follow_symlinks", set()):
            with self.assertRaises(fsops.Unsupported):
                fsops.require_support()

    def test_a_missing_flag_or_call_is_reported_by_name(self):
        for name in ("O_DIRECTORY", "O_NOFOLLOW", "fchmod"):
            with self.subTest(name):
                saved = getattr(fsops.os, name)
                delattr(fsops.os, name)
                try:
                    with self.assertRaises(fsops.Unsupported) as ctx:
                        fsops.require_support()
                finally:
                    setattr(fsops.os, name, saved)
                self.assertIn(name, str(ctx.exception))


class DescendTests(FsopsCase):
    def test_a_symlink_is_never_followed_and_a_plain_file_is_not_a_directory(self):
        os.symlink(self.outside, os.path.join(self.root, "link"))
        with open(os.path.join(self.root, "file"), "w"):
            pass
        for name in ("link", "file"):
            with self.subTest(name):
                with self.assertRaises(fsops.UnsafePath):
                    self.tree.descend(self.tree.root, name)

    def test_a_missing_directory_is_created_only_on_request_and_remembered(self):
        with self.assertRaises(FileNotFoundError):
            self.tree.descend(self.tree.root, "new")
        made = self.tree.descend(self.tree.root, "new", create=True)
        self.assertTrue(os.path.isdir(os.path.join(self.root, "new")))
        self.assertEqual(self.tree.created, [made])
        again = self.tree.descend(self.tree.root, "new", create=True)
        self.assertEqual(self.tree.created, [made], "an existing directory is not ours")
        self.assertEqual(again.ident, made.ident)

    def test_dir_of_finds_the_root_a_subdirectory_and_a_directory_elsewhere(self):
        os.mkdir(os.path.join(self.root, "sub"))
        self.assertIs(self.tree.dir_of(self.root), self.tree.root)
        self.assertEqual(self.tree.dir_of(os.path.join(self.root, "sub")).name, "sub")
        sibling = self.root + "x"  # shares the root's name as a prefix, but is not inside it
        os.mkdir(sibling)
        self.assertEqual(self.tree.dir_of(sibling).abs_path, sibling)
        elsewhere = self.tree.dir_of(self.outside)
        self.assertEqual(elsewhere.abs_path, self.outside)
        self.tree.revalidate(elsewhere)

    def test_root_that_is_not_a_plain_directory_is_refused(self):
        with self.assertRaises(OSError):
            fsops.Tree(os.path.join(self.base, "missing"))


class RevalidateTests(FsopsCase):
    def test_a_swapped_component_is_detected_at_every_level(self):
        os.makedirs(os.path.join(self.root, "a", "b"))
        deep = self.tree.descend(self.tree.descend(self.tree.root, "a"), "b")
        self.tree.revalidate(deep)
        os.rename(os.path.join(self.root, "a"), os.path.join(self.root, "a.real"))
        with self.assertRaises(fsops.UnsafePath):
            self.tree.revalidate(deep)  # "a" is gone
        os.symlink(self.outside, os.path.join(self.root, "a"))
        with self.assertRaises(fsops.UnsafePath):
            self.tree.revalidate(deep)  # "a" is a symlink

    def test_a_different_directory_with_the_same_name_is_detected(self):
        os.mkdir(os.path.join(self.root, "a"))
        held = self.tree.descend(self.tree.root, "a")
        os.rename(os.path.join(self.root, "a"), os.path.join(self.root, "a.real"))
        os.mkdir(os.path.join(self.root, "a"))
        with self.assertRaises(fsops.UnsafePath):
            self.tree.revalidate(held)

    def test_a_swapped_root_is_detected(self):
        os.rename(self.root, self.root + ".real")
        os.mkdir(self.root)
        with self.assertRaises(fsops.UnsafePath):
            self.tree.revalidate(self.tree.root)


class OwnershipTests(FsopsCase):
    def test_a_created_file_is_removed_through_the_held_directory(self):
        created = fsops.create_exclusive(self.tree.root, ".stage", b"data", "100644")
        self.assertTrue(fsops.remove_created(created))
        self.assertFalse(os.path.exists(os.path.join(self.root, ".stage")))

    def test_a_file_that_replaced_the_created_one_is_not_removed(self):
        created = fsops.create_exclusive(self.tree.root, ".stage", b"data", "100644")
        path = os.path.join(self.root, ".stage")
        os.unlink(path)
        before = self.user_file(path)
        self.assertFalse(fsops.remove_created(created))
        self.assertEqual(snap(path), before)

    def test_a_symlink_or_directory_at_the_name_is_not_removed(self):
        created = fsops.create_exclusive(self.tree.root, ".stage", b"data", "100644")
        path = os.path.join(self.root, ".stage")
        os.unlink(path)
        target = os.path.join(self.outside, "t")
        before = self.user_file(target)
        os.symlink(target, path)
        self.assertFalse(fsops.remove_created(created))
        self.assertEqual(snap(target), before)
        self.assertTrue(os.path.islink(path))

    def test_removal_follows_the_held_directory_not_the_path(self):
        os.mkdir(os.path.join(self.root, "d"))
        held = self.tree.descend(self.tree.root, "d")
        created = fsops.create_exclusive(held, ".stage", b"data", "100644")
        before = self.user_file(os.path.join(self.outside, ".stage"))
        os.rename(os.path.join(self.root, "d"), os.path.join(self.root, "d.real"))
        os.symlink(self.outside, os.path.join(self.root, "d"))
        self.assertTrue(fsops.remove_created(created))
        self.assertEqual(snap(os.path.join(self.outside, ".stage")), before)
        self.assertFalse(os.path.exists(os.path.join(self.root, "d.real", ".stage")))

    def test_a_renamed_file_is_no_longer_ours_to_remove(self):
        created = fsops.create_exclusive(self.tree.root, ".stage", b"data", "100644")
        fsops.replace_created(created, "final")
        before = self.user_file(os.path.join(self.root, ".stage"))
        self.assertFalse(fsops.remove_created(created))
        self.assertEqual(snap(os.path.join(self.root, ".stage")), before)
        with open(os.path.join(self.root, "final"), "rb") as handle:
            self.assertEqual(handle.read(), b"data")

    def test_replacing_a_swapped_staged_file_is_refused(self):
        created = fsops.create_exclusive(self.tree.root, ".stage", b"data", "100644")
        path = os.path.join(self.root, ".stage")
        os.unlink(path)
        before = self.user_file(path)
        with self.assertRaises(fsops.UnsafePath):
            fsops.replace_created(created, "final")
        self.assertEqual(snap(path), before)
        self.assertFalse(os.path.exists(os.path.join(self.root, "final")))

    def test_created_directories_are_removed_only_while_they_are_the_ones_made(self):
        mine = self.tree.descend(self.tree.root, "mine", create=True)
        theirs = self.tree.descend(self.tree.root, "theirs", create=True)
        self.assertEqual(self.tree.created, [mine, theirs])
        os.rename(os.path.join(self.root, "theirs"), os.path.join(self.root, "theirs.real"))
        os.mkdir(os.path.join(self.root, "theirs"), 0o750)
        self.tree.remove_created_dirs()
        self.assertFalse(os.path.exists(os.path.join(self.root, "mine")))
        self.assertTrue(os.path.isdir(os.path.join(self.root, "theirs")), "a directory the run did not make was removed")
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.root, "theirs")).st_mode), 0o750)
        self.assertEqual(self.tree.created, [])

    def test_nested_created_directories_are_removed_innermost_first(self):
        outer = self.tree.descend(self.tree.root, "outer", create=True)
        self.tree.descend(outer, "inner", create=True)
        self.tree.remove_created_dirs()
        self.assertFalse(os.path.exists(os.path.join(self.root, "outer")))

    def test_a_created_directory_that_gained_content_is_kept(self):
        made = self.tree.descend(self.tree.root, "made", create=True)
        before = self.user_file(os.path.join(self.root, "made", "keep"))
        self.tree.remove_created_dirs()
        self.assertEqual(snap(os.path.join(self.root, "made", "keep")), before)
        self.assertIsNotNone(made)


class CreateTests(FsopsCase):
    def test_the_inode_of_a_created_file_stays_pinned_until_it_is_closed(self):
        created = fsops.create_exclusive(self.tree.root, ".stage", b"data", "100644")
        fd = created.fd
        os.unlink(os.path.join(self.root, ".stage"))
        self.assertEqual(os.fstat(fd).st_nlink, 0, "the descriptor must still hold the unlinked file")
        created.close()
        self.assertIsNone(created.fd)
        with self.assertRaises(OSError):
            os.fstat(fd)
        created.close()  # closing twice is harmless

    def test_a_taken_name_is_skipped_and_left_exactly_as_it_was(self):
        path = os.path.join(self.root, ".stage")
        before = self.user_file(path)
        created = fsops.create_exclusive(self.tree.root, ".stage", b"data", "100755")
        self.assertEqual(created.name, ".stage.1")
        self.assertEqual(snap(path), before)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.root, ".stage.1")).st_mode), 0o755)

    def test_a_dangling_symlink_at_the_name_is_taken_not_followed(self):
        os.symlink(os.path.join(self.outside, "nowhere"), os.path.join(self.root, ".stage"))
        created = fsops.create_exclusive(self.tree.root, ".stage", b"data", "100644")
        self.assertEqual(created.name, ".stage.1")
        self.assertEqual(os.listdir(self.outside), [])

    def test_every_name_taken_is_an_error_and_changes_nothing(self):
        for k in range(3):
            self.user_file(os.path.join(self.root, ".stage" if k == 0 else ".stage.%d" % k))
        before = sorted(os.listdir(self.root))
        with self.assertRaises(FileExistsError):
            fsops.create_exclusive(self.tree.root, ".stage", b"data", "100644", attempts=3)
        self.assertEqual(sorted(os.listdir(self.root)), before)

    def test_mode_is_set_explicitly_whatever_the_umask(self):
        old = os.umask(0o077)
        try:
            plain = fsops.create_exclusive(self.tree.root, "p", b"x", "100644")
            exe = fsops.create_exclusive(self.tree.root, "e", b"x", "100755")
        finally:
            os.umask(old)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.root, plain.name)).st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.root, exe.name)).st_mode), 0o755)

    def test_a_failed_write_removes_only_the_file_it_created_and_closes_it(self):
        before = len(os.listdir("/dev/fd"))
        with mock.patch.object(fsops.os, "fchmod", side_effect=OSError(errno.EPERM, "denied")):
            with self.assertRaises(OSError):
                fsops.create_exclusive(self.tree.root, ".stage", b"data", "100644")
        self.assertEqual(os.listdir(self.root), [])
        self.assertEqual(len(os.listdir("/dev/fd")), before, "the descriptor of the failed file leaked")


if __name__ == "__main__":
    unittest.main()
