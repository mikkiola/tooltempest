"""The pinned-checker launch check `scripts/tt_pin_check.py` (ADR-0022, Decision 5).

Two layers. The decision is a pure function of five facts, and its complete table (32 rows) is asserted row by
row. The facts are then read from a real temporary consumer that holds a real materialized payload: the files this
checkout's MANIFEST.txt installs are committed to a throwaway "ToolTempest", and the real `tooling.py` installs
them into a consumer. The reachable outcomes (skip, pass, missing, tooling, files, receipt) are each produced by
damaging the real install in that one way and asserted by the exit code and the reason printed; the cells that
combine two failures are decided by the order in `decide()`, which the table pins. Linked worktrees, a main
checkout that is behind, the effective index, and a source without the pin are covered. The check itself must change nothing it reads and fetch nothing.
"""
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tooling_helpers import (REPO_ROOT, SCRIPTS_DIR, Consumer, Upstream, clean_env, run_tooling, sha256, tree_digest)

import tt_pin_check

CHECK = SCRIPTS_DIR / "tt_pin_check.py"
SCAN = SCRIPTS_DIR / "check_mutable_reads.py"
ZERO = "0" * 40
INSTALLED = ("scripts/", "schemas/", "docs/tooltempest/")

# present, tooling, files, receipt -> verdict, for a lock that exists (the full table: 16 rows)
TABLE = [
    (True, True, True, True, "pass"),
    (True, True, True, False, "receipt"),
    (True, True, False, True, "files"),
    (True, True, False, False, "files"),
    (True, False, True, True, "tooling"),
    (True, False, True, False, "tooling"),
    (True, False, False, True, "tooling"),
    (True, False, False, False, "tooling"),
] + [(False,) + rest + ("missing",) for rest in itertools.product((True, False), repeat=3)]


class DecisionTable(unittest.TestCase):
    def test_a_lock_that_exists_gives_the_verdict_of_the_first_fact_that_does_not_hold(self):
        self.assertEqual(len(TABLE), 16)
        self.assertEqual(len({row[:4] for row in TABLE}), 16, "each combination exactly once")
        for present, tooling, files, receipt, verdict in TABLE:
            with self.subTest(present=present, tooling=tooling, files=files, receipt=receipt):
                self.assertEqual(tt_pin_check.decide(True, present, tooling, files, receipt), verdict)

    def test_without_a_lock_every_combination_is_skipped(self):
        for facts in itertools.product((True, False), repeat=4):
            with self.subTest(facts=facts):
                self.assertEqual(tt_pin_check.decide(False, *facts), "skip")


def payload():
    """The files this checkout installs into a consumer, from the working tree (so an uncommitted change is tested)."""
    with open(REPO_ROOT / "MANIFEST.txt", encoding="utf-8") as handle:
        paths = [line.strip() for line in handle if line.strip().startswith(INSTALLED)]
    return {path: (REPO_ROOT / path).read_bytes() for path in paths}


def slurp(path):
    with open(path, "rb") as handle:
        return handle.read()


def spit(path, data):
    with open(path, "wb") as handle:
        handle.write(data)


def edit_json(target, fields):
    with open(target, encoding="utf-8") as handle:
        data = json.load(handle)
    data.update(fields)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    return data


def checker_of(files):
    return sha256(files["scripts/docops.py"])


class RealInstall(unittest.TestCase):
    """A real consumer with a real install of the payload, copied per test from a template built once."""

    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.mkdtemp(prefix="pin-home-")
        cls.upstream = Upstream()
        cls.files = payload()
        cls.pin = cls.upstream.publish(cls.files, message="pin 1")
        files2 = dict(cls.files, **{"scripts/docops.py": cls.files["scripts/docops.py"] + b"# pin 2\n"})
        cls.pin2 = cls.upstream.publish(files2, message="pin 2")
        cls.files2 = files2
        cls.template = Consumer()
        cls.install(cls.template.root, cls.pin, cls.files)
        cls.stub = Upstream()  # a source that holds no commit of the payload

    @classmethod
    def install(cls, root, pin, files):
        consumer = Consumer.__new__(Consumer)
        consumer.root = root
        consumer.lock_path = os.path.join(root, ".tooltempest.lock")
        consumer.write_lock(pin, repository=cls.upstream.url, legacy=("scripts/docops.py", checker_of(files)))
        code, out, err = run_tooling(["materialize", "--root", root, "--source", cls.upstream.root, "--apply",
                                      "--format", "json"], cls.home)
        assert code == 0, out + err
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "install " + pin[:7])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home, ignore_errors=True)
        for fixture in (cls.upstream, cls.template, cls.stub):
            fixture.cleanup()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pin-case-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = os.path.realpath(os.path.join(self.tmp, "consumer"))
        shutil.copytree(self.template.root, self.root, symlinks=True)

    # -- running --------------------------------------------------------------
    def check(self, mode="commit", stdin="", root=None, source=None, env=None):
        proc = subprocess.run([sys.executable, "-I", str(CHECK), mode, "--root", root or self.root, "--source",
                               source or self.upstream.root], input=stdin, capture_output=True, text=True,
                              env=clean_env(dict(env or {}, HOME=self.home)), timeout=180)
        return proc.returncode, proc.stdout + proc.stderr

    def damage(self, rel, data=b"# changed\n", root=None):
        with open(os.path.join(root or self.root, rel), "ab") as handle:
            handle.write(data)

    def stage_lock(self, **fields):
        edit_json(os.path.join(self.root, ".tooltempest.lock"), fields)
        git(self.root, "add", ".tooltempest.lock")

    def head(self, root=None):
        return git(root or self.root, "rev-parse", "HEAD")


def git(root, *args, env=None):
    proc = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, env=env or clean_env())
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


class OneFactAtATime(RealInstall):
    """One cell of the table per test, made by damaging the real install in exactly that way."""

    def test_all_facts_hold_passes_and_prints_every_path_and_hash_it_used(self):
        rc, out = self.check()
        self.assertEqual(rc, 0, out)
        docops, tooling = os.path.join(self.root, "scripts/docops.py"), os.path.join(self.root, "scripts/tooling.py")
        self.assertIn(docops, out)
        self.assertEqual(out.count(checker_of(self.files)), 1 + 1, "installed and declared")
        self.assertIn(tooling, out)
        self.assertEqual(out.count(sha256(self.files["scripts/tooling.py"])), 2, "installed and pinned")
        self.assertIn(self.pin[:12], out)
        self.assertIn("verify exit 0", out)

    def test_no_lock_in_the_index_is_skipped_with_a_message(self):
        git(self.root, "rm", "-q", "--cached", ".tooltempest.lock")
        rc, out = self.check()
        self.assertEqual((rc, "not a consumer tip" in out, "verify exit" in out), (0, True, False), out)

    def test_a_missing_file_blocks_and_names_its_path(self):
        for rel in ("scripts/docops.py", "scripts/tooling.py", ".tooltempest.receipt.json"):
            with self.subTest(rel):
                path = os.path.join(self.root, rel)
                saved = slurp(path)
                os.remove(path)
                rc, out = self.check()
                spit(path, saved)
                self.assertEqual(rc, 1, out)
                self.assertIn("%s is missing" % path, out)
                self.assertIn("To fix: python3", out)

    def test_a_declared_file_that_is_missing_blocks(self):
        self.stage_lock(files=[{"path": "scripts/docops_core/checks.py", "sha256": sha256(self.files["scripts/docops_core/checks.py"])}])
        os.remove(os.path.join(self.root, "scripts/docops_core/checks.py"))
        rc, out = self.check()
        self.assertEqual(rc, 1, out)
        self.assertIn("scripts/docops_core/checks.py is missing", out)

    def test_a_changed_tooling_py_blocks_before_it_is_run(self):
        marker = os.path.join(self.tmp, "tooling-ran")
        path = os.path.join(self.root, "scripts/tooling.py")
        spit(path, ("open(%r, 'w').write('ran')\n" % marker).encode() + slurp(path))  # runs first, whatever follows
        rc, out = self.check()
        self.assertEqual(rc, 1, out)
        self.assertIn("scripts/tooling.py differs from the pinned one", out)
        self.assertFalse(os.path.exists(marker), "the changed tooling.py was executed")
        self.assertNotIn("verify exit", out)

    def test_a_changed_checker_blocks_with_the_materialize_command(self):
        self.damage("scripts/docops.py")
        rc, out = self.check()
        self.assertEqual(rc, 1, out)
        self.assertIn("scripts/docops.py differs from the lock", out)
        self.assertIn("materialize --root %s" % self.root, out)

    def test_a_declared_file_with_another_hash_blocks(self):
        self.stage_lock(files=[{"path": "scripts/docops_core/checks.py", "sha256": "f" * 64}])
        rc, out = self.check()
        self.assertEqual(rc, 1, out)
        self.assertIn("scripts/docops_core/checks.py differs from the lock", out)

    def test_a_lock_that_does_not_declare_the_checker_blocks(self):
        self.stage_lock(path="scripts/tooling.py", sha256=sha256(self.files["scripts/tooling.py"]))
        rc, out = self.check()
        self.assertEqual(rc, 1, out)
        self.assertIn("scripts/docops.py differs from the lock, or the lock does not declare it", out)

    def test_an_installed_file_the_lock_does_not_declare_but_the_receipt_covers_blocks(self):
        self.damage("scripts/docops_core/checks.py")
        rc, out = self.check()
        self.assertEqual(rc, 1, out)
        self.assertIn("`tooling.py verify` exited 1", out)

    def test_a_receipt_that_does_not_match_the_pin_blocks(self):
        receipt = os.path.join(self.root, ".tooltempest.receipt.json")
        with open(receipt, encoding="utf-8") as handle:
            files = json.load(handle)["files"]
        files[0]["sha256"] = "e" * 64
        edit_json(receipt, {"files": files})
        rc, out = self.check()
        self.assertEqual(rc, 1, out)
        self.assertIn("`tooling.py verify` exited 1", out)

    def test_an_unusable_lock_blocks(self):
        for bad in (b"not json", b'{"repository": "x", "commit": "main"}'):
            with self.subTest(bad):
                path = os.path.join(self.root, ".tooltempest.lock")
                spit(path, bad)
                git(self.root, "add", ".tooltempest.lock")
                rc, out = self.check()
                self.assertEqual(rc, 1, out)
                self.assertIn("the lock is not usable", out)

    def test_a_pin_that_the_source_does_not_hold_blocks_and_is_never_fetched(self):
        # the lock names a repository that holds the commit, so a fetch would succeed; it must not be tried
        rc, out = self.check(source=self.stub.root)
        self.assertEqual(rc, 1, out)
        self.assertIn("cannot read the pinned scripts/tooling.py", out)
        self.assertIn("--allow-network to fetch exactly %s" % self.pin, out)
        probe = subprocess.run(["git", "-C", self.stub.root, "cat-file", "-e", self.pin], capture_output=True, env=clean_env())
        self.assertNotEqual(probe.returncode, 0, "the pinned commit appeared in the source")

    def test_a_missing_source_blocks(self):
        rc, out = self.check(source=os.path.join(self.tmp, "no-such-source"))
        self.assertEqual(rc, 1, out)
        self.assertIn("cannot read the pinned", out)

    def test_the_check_writes_nothing_in_the_repository_or_the_source(self):
        before = tree_digest(self.root), tree_digest(self.upstream.root)
        for mode, stdin in (("commit", ""), ("push", "refs/heads/main %s refs/heads/main %s\n" % (self.head(), ZERO))):
            rc, out = self.check(mode, stdin)
            self.assertEqual(rc, 0, out)
        self.assertEqual((tree_digest(self.root), tree_digest(self.upstream.root)), before)
        self.assertEqual(git(self.root, "status", "--porcelain"), "")

    def test_a_configured_fsmonitor_program_is_never_started(self):
        marker = os.path.join(self.tmp, "fsmonitor-ran")
        program = os.path.join(self.tmp, "fsmonitor.sh")
        spit(program, ("#!/bin/sh\ntouch %s\nprintf '\\0'\n" % marker).encode())
        os.chmod(program, 0o755)
        git(self.root, "config", "core.fsmonitor", program)
        before = tree_digest(self.root)
        for mode, stdin in (("commit", ""), ("push", "refs/heads/main %s refs/heads/main %s\n" % (self.head(), ZERO))):
            rc, out = self.check(mode, stdin)
            self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(marker), "the fsmonitor program was started")
        self.assertEqual(tree_digest(self.root), before)


class EffectiveIndex(RealInstall):
    def test_the_lock_comes_from_the_index_not_from_head_or_the_working_tree(self):
        self.stage_lock(sha256="f" * 64)  # staged: another declared hash
        rc, _ = self.check()
        self.assertEqual(rc, 1)
        self.stage_lock(sha256=checker_of(self.files))
        os.remove(os.path.join(self.root, ".tooltempest.lock"))  # working tree has no lock, the index is good
        self.assertEqual(self.check()[0], 0)

    def test_git_index_file_selects_the_index_of_the_commit_in_progress(self):
        alt = os.path.join(self.tmp, "alt-index")
        env = clean_env({"GIT_INDEX_FILE": alt})
        git(self.root, "read-tree", "HEAD", env=env)
        git(self.root, "rm", "-q", "--cached", ".tooltempest.lock", env=env)
        rc, out = self.check(env={"GIT_INDEX_FILE": alt})
        self.assertEqual((rc, "not a consumer tip" in out), (0, True), out)
        rc, out = self.check()
        self.assertEqual((rc, "verify exit 0" in out), (0, True), out)

    def test_other_inherited_git_variables_do_not_redirect_the_check(self):
        rc, out = self.check(env={"GIT_DIR": os.path.join(self.tmp, "nowhere"), "GIT_WORK_TREE": self.tmp})
        self.assertEqual(rc, 0, out)

    def test_an_unmerged_lock_blocks(self):
        git(self.root, "checkout", "-q", "-b", "other")
        self.stage_lock(sha256="1" * 64)
        git(self.root, "commit", "-q", "-m", "other lock")
        git(self.root, "checkout", "-q", "main")
        self.stage_lock(sha256="2" * 64)
        git(self.root, "commit", "-q", "-m", "main lock")
        subprocess.run(["git", "-C", self.root, "merge", "other"], capture_output=True, env=clean_env())
        rc, out = self.check()
        self.assertEqual(rc, 1, out)
        self.assertIn("unmerged, or is a symlink or a submodule", out)


class Worktrees(RealInstall):
    def add_worktree(self, name="wt"):
        wt = os.path.join(self.tmp, name)
        git(self.root, "worktree", "add", "-q", "-b", name, wt)
        return os.path.realpath(wt)

    def test_a_linked_worktree_is_checked_on_its_own_files(self):
        wt = self.add_worktree()
        rc, out = self.check(root=wt)
        self.assertEqual(rc, 0, out)
        self.assertIn(os.path.join(wt, "scripts/docops.py"), out)
        self.assertNotIn(self.root, out)

    def test_a_linked_worktree_never_falls_back_to_the_main_checkout(self):
        wt = self.add_worktree()
        os.remove(os.path.join(wt, "scripts/docops.py"))
        rc, out = self.check(root=wt)
        self.assertEqual(rc, 1, out)
        self.assertIn("%s is missing" % os.path.join(wt, "scripts/docops.py"), out)
        self.assertNotIn(os.path.join(self.root, "scripts/docops.py"), out)
        self.assertEqual(self.check()[0], 0, "the intact main checkout still passes: the failure was the worktree's own")

    def test_a_worktree_on_a_newer_pin_is_not_judged_by_the_main_checkout_that_is_behind(self):
        wt = self.add_worktree()
        self.install(wt, self.pin2, self.files2)  # the worktree is repinned and reinstalled; the main checkout stays on pin 1
        self.assertNotEqual(slurp(os.path.join(wt, "scripts/docops.py")), slurp(os.path.join(self.root, "scripts/docops.py")))
        rc, out = self.check(root=wt)
        self.assertEqual(rc, 0, out)
        self.assertIn(checker_of(self.files2), out)
        self.assertNotIn(checker_of(self.files), out)
        rc, out = self.check()
        self.assertEqual(rc, 0, out)
        self.assertIn(checker_of(self.files), out)
        self.assertNotIn(checker_of(self.files2), out)

    def test_the_stale_checker_of_the_main_checkout_is_refused_in_a_repinned_worktree(self):
        wt = self.add_worktree()
        self.install(wt, self.pin2, self.files2)
        shutil.copyfile(os.path.join(self.root, "scripts/docops.py"), os.path.join(wt, "scripts/docops.py"))  # the old fallback
        rc, out = self.check(root=wt)
        self.assertEqual(rc, 1, out)
        self.assertIn("scripts/docops.py differs from the lock", out)


class Push(RealInstall):
    def line(self, ref, sha):
        return "%s %s %s %s\n" % (ref, sha, ref, ZERO)

    def test_two_tips_with_the_same_lock_pass(self):
        git(self.root, "branch", "other")
        rc, out = self.check("push", self.line("refs/heads/main", self.head()) + self.line("refs/heads/other", self.head()))
        self.assertEqual(rc, 0, out)
        self.assertIn("verify exit 0", out)

    def test_two_tips_with_different_locks_are_refused(self):
        git(self.root, "checkout", "-q", "-b", "repinned")
        self.stage_lock(files=[{"path": "scripts/check_manifest.py", "sha256": "1" * 64}])
        git(self.root, "commit", "-q", "-m", "other lock")
        other = self.head()
        git(self.root, "checkout", "-q", "main")
        rc, out = self.check("push", self.line("refs/heads/main", self.head()) + self.line("refs/heads/repinned", other))
        self.assertEqual(rc, 1, out)
        self.assertIn("push them separately", out)
        self.assertIn("refs/heads/repinned", out)

    def test_a_deletion_and_an_empty_input_have_nothing_to_check(self):
        rc, out = self.check("push", "(delete) %s refs/heads/old %s\n" % (ZERO, self.head()))
        self.assertEqual((rc, "nothing to push" in out), (0, True), out)
        rc, out = self.check("push", "")
        self.assertEqual((rc, "nothing to push" in out), (0, True), out)

    def test_a_tip_without_a_lock_is_skipped_and_the_other_tip_is_checked(self):
        git(self.root, "checkout", "-q", "--orphan", "vault")
        git(self.root, "rm", "-rq", "--cached", ".")
        with open(os.path.join(self.root, "note.txt"), "w") as handle:
            handle.write("data\n")
        git(self.root, "add", "note.txt")
        git(self.root, "commit", "-q", "-m", "data only")
        vault = self.head()
        git(self.root, "checkout", "-q", "-f", "main")
        rc, out = self.check("push", self.line("refs/heads/vault", vault) + self.line("refs/heads/main", self.head()))
        self.assertEqual(rc, 0, out)
        self.assertIn("refs/heads/vault", out)
        self.assertIn("not a consumer tip", out)
        self.assertIn("verify exit 0", out)

    def test_an_annotated_tag_is_checked_through_the_commit_it_names(self):
        git(self.root, "tag", "-a", "-m", "release", "v1")
        tag = git(self.root, "rev-parse", "refs/tags/v1")
        self.assertNotEqual(tag, self.head())
        rc, out = self.check("push", self.line("refs/tags/v1", tag))
        self.assertEqual((rc, "verify exit 0" in out), (0, True), out)
        self.damage("scripts/docops.py")
        rc, out = self.check("push", self.line("refs/tags/v1", tag))
        self.assertEqual(rc, 1, out)

    def test_an_annotated_tag_of_a_commit_without_a_lock_is_skipped(self):
        git(self.root, "checkout", "-q", "--orphan", "vault")
        git(self.root, "rm", "-rq", "--cached", ".")
        git(self.root, "commit", "-q", "--allow-empty", "-m", "data only")
        git(self.root, "tag", "-a", "-m", "data", "d1")
        tag = git(self.root, "rev-parse", "refs/tags/d1")
        git(self.root, "checkout", "-q", "-f", "main")
        rc, out = self.check("push", self.line("refs/tags/d1", tag))
        self.assertEqual((rc, "not a consumer tip" in out), (0, True), out)

    def test_a_line_that_is_not_four_fields_is_refused(self):
        rc, out = self.check("push", "refs/heads/main %s\n" % self.head())
        self.assertEqual(rc, 1, out)
        self.assertIn("not a pre-push line", out)

    def test_the_installed_checker_that_would_run_is_judged_whatever_the_tip_holds(self):
        self.damage("scripts/docops.py")  # the tip holds the good file, the file that would run is damaged
        rc, out = self.check("push", self.line("refs/heads/main", self.head()))
        self.assertEqual(rc, 1, out)
        self.assertIn("scripts/docops.py differs from the lock", out)


class Usage(RealInstall):
    def test_bad_usage_and_an_internal_failure_do_not_pass(self):
        self.assertEqual(self.check(root=os.path.join(self.tmp, "does-not-exist"))[0], 2)
        for argv in (["bogus"], ["commit"], ["commit", "--root"]):
            with self.subTest(argv):
                proc = subprocess.run([sys.executable, "-I", str(CHECK), *argv], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 2)
        self.assertEqual(subprocess.run([sys.executable, "-I", str(CHECK), "--help"], capture_output=True).returncode, 0)

    def test_the_script_has_no_finding_of_the_mutable_read_scan(self):
        proc = subprocess.run([sys.executable, "-I", str(SCAN), str(CHECK)], capture_output=True, text=True)
        self.assertEqual((proc.returncode, "0 finding(s)" in proc.stdout), (0, True), proc.stdout + proc.stderr)

    def test_the_script_is_listed_in_the_manifest_and_installed_by_materialize(self):
        self.assertIn("scripts/tt_pin_check.py", self.files)
        self.assertEqual(slurp(os.path.join(self.root, "scripts/tt_pin_check.py")), self.files["scripts/tt_pin_check.py"])


if __name__ == "__main__":
    unittest.main()
