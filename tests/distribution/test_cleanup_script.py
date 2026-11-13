"""`scripts/scratch_wt_exec2.sh`, the gated worktree cleanup (universal rules, "Worktree lifecycle").

The script reads the process table and open files, so these tests run on macOS only (it is written for macOS bash
3.2) and use throwaway repositories under /private/tmp. They check the contract: what a dry run changes, what
`apply` removes, what it refuses, and that it never uses a force option. Each gate has a test that makes only that
gate fail, and a failure of the process table or of the open-file listing (`ps`, `lsof`: unavailable, exit non-zero,
or no output) must skip the item with the reason, never let it through.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

from dist_helpers import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "scratch_wt_exec2.sh"


def clean_env(extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
    env.update(extra or {})
    return env


def git(cwd, *args):
    proc = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                           "-c", "core.hooksPath=/dev/null", *args], cwd=cwd, capture_output=True, text=True, env=clean_env())
    if proc.returncode != 0:
        raise AssertionError("git %s: %s" % (" ".join(args), proc.stderr))
    return proc.stdout.strip()


@unittest.skipUnless(sys.platform == "darwin" and os.path.isdir("/private/tmp"), "macOS only")
class CleanupScriptTests(unittest.TestCase):
    def setUp(self):
        self.t = os.path.realpath(tempfile.mkdtemp(prefix="ttwt-", dir="/private/tmp"))
        self.addCleanup(shutil.rmtree, self.t, True)
        self.ws = os.path.join(self.t, "ws")
        self.bk = os.path.join(self.t, "bk")
        self.scratch = os.path.join(self.t, "scratch")
        origin = os.path.join(self.t, "origin.git")
        os.makedirs(self.ws)
        git(self.t, "init", "-q", "--bare", "-b", "main", origin)
        self.repo = os.path.join(self.ws, "r1")
        git(self.t, "clone", "-q", origin, self.repo)
        with open(os.path.join(self.repo, "a.txt"), "w") as fh:
            fh.write("1\n")
        with open(os.path.join(self.repo, ".gitignore"), "w") as fh:
            fh.write("*.log\n__pycache__/\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "one")
        git(self.repo, "push", "-q", "origin", "HEAD:main")
        git(self.repo, "fetch", "-q", "origin")
        git(self.repo, "branch", "--set-upstream-to=origin/main", "main")
        # a unique token: the script treats a process that mentions the session directory as a user of it
        self.suffix = "sid%s/s2/scratchpad/wt" % uuid.uuid4().hex
        self.wt = os.path.join(self.scratch, self.suffix)
        git(self.repo, "worktree", "add", "-q", "-b", "feat/x", self.wt)
        self.storage = os.path.join(self.t, "storage.json")
        open(self.storage, "w").close()
        self.work = os.path.join(self.t, "worklist.txt")
        self.write_worklist()

    def write_worklist(self):
        head = git(self.wt, "rev-parse", "--short", "HEAD")
        with open(self.work, "w") as fh:
            fh.write("# one item\n\nr1|%s|%s|feat/x\n" % (self.suffix, head))

    def bundle(self):
        os.makedirs(os.path.join(self.bk, "r1"), exist_ok=True)
        git(self.repo, "bundle", "create", os.path.join(self.bk, "r1", "backup-branches.bundle"), "--all")

    NEEDED = ("git", "grep", "cut", "awk", "sort", "date", "mktemp", "shasum", "wc", "tr", "head", "rm", "ps", "lsof")

    def stub_bin(self, name, body=None):
        """A bin directory that holds every tool the script needs; `name` is replaced by a stub that runs `body`
        (a shell snippet), or left out entirely when `body` is None. Returns the PATH value."""
        d = os.path.join(self.t, "bin-" + name + ("-gone" if body is None else "-stub"))
        os.makedirs(d, exist_ok=True)
        for tool in self.NEEDED:
            real = shutil.which(tool) or shutil.which(tool, path="/usr/sbin:/usr/bin:/bin")
            if tool == name:
                if body is not None:
                    with open(os.path.join(d, tool), "w") as fh:
                        fh.write("#!/bin/bash\n%s\n" % body)
                    os.chmod(os.path.join(d, tool), 0o755)
            elif real and not os.path.exists(os.path.join(d, tool)):
                os.symlink(real, os.path.join(d, tool))
        return d

    def assert_skipped_with_reason(self, out, tool):
        self.assertTrue(os.path.isdir(self.wt), out)
        self.assertEqual(git(self.repo, "branch", "--list", "feat/x").strip().lstrip("+ "), "feat/x")
        self.assertNotIn("all gates pass", out)
        self.assertIn("SKIP: [r1] %s" % self.wt, out)
        self.assertIn("%s unusable" % tool, out)

    def run_script(self, mode, env_extra=None, worklist=None):
        env = clean_env({"TT_WORKSPACE": self.ws, "TT_BACKUP_DIR": self.bk, "TT_SCRATCH_PREFIX": self.scratch + "/",
                         "TT_VSCODE_STORAGE": self.storage})
        env.update(env_extra or {})
        for k in [k for k, v in env.items() if v is None]:
            del env[k]
        args = ["/bin/bash", str(SCRIPT), mode] + ([worklist or self.work] if worklist != "" else [])
        proc = subprocess.run(args, capture_output=True, text=True, env=env, timeout=170)
        return proc.returncode, proc.stdout + proc.stderr

    def test_the_script_is_valid_bash_3_2_and_uses_no_force_option_or_associative_array(self):
        self.assertEqual(subprocess.run(["/bin/bash", "-n", str(SCRIPT)], capture_output=True).returncode, 0)
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("declare -A", text)
        self.assertNotRegex(text, r"branch -D|worktree remove --force|worktree remove -f|--force")
        self.assertTrue(os.access(SCRIPT, os.X_OK))

    def test_usage_errors_stop_before_anything_is_read(self):
        for env_extra, worklist, needle in (({}, "", "usage"), ({"TT_WORKSPACE": None}, None, "TT_WORKSPACE"),
                                            ({"TT_BACKUP_DIR": None}, None, "TT_BACKUP_DIR"), ({}, "/does/not/exist", "usage")):
            with self.subTest(case=needle):
                rc, out = self.run_script("dry", env_extra, worklist)
                self.assertEqual(rc, 2, out)
                self.assertIn(needle, out)
        rc, out = self.run_script("bogus")
        self.assertEqual(rc, 2)
        self.assertTrue(os.path.isdir(self.wt))

    def test_a_dry_run_changes_nothing_and_names_the_planned_commands(self):
        self.bundle()
        rc, out = self.run_script("dry")
        self.assertEqual(rc, 0, out)
        self.assertIn("all gates pass", out)
        self.assertIn("worktree remove %s" % self.wt, out)
        self.assertIn("DRY RUN: nothing changed", out)
        self.assertTrue(os.path.isdir(self.wt))
        self.assertIn("feat/x", git(self.repo, "branch", "--list", "feat/x"))

    def test_apply_removes_a_clean_integrated_worktree_and_its_branch(self):
        self.bundle()
        rc, out = self.run_script("apply")
        self.assertEqual(rc, 0, out)
        self.assertFalse(os.path.exists(self.wt))
        self.assertEqual(git(self.repo, "branch", "--list", "feat/x"), "")
        self.assertIn("=== skipped: none", out)

    def test_uncommitted_changes_unintegrated_work_a_missing_backup_and_a_used_worktree_are_all_skipped(self):
        self.bundle()
        with open(os.path.join(self.wt, "new.txt"), "w") as fh:
            fh.write("untracked\n")
        rc, out = self.run_script("apply")
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.isdir(self.wt))
        self.assertIn("blocking entry", out)
        os.remove(os.path.join(self.wt, "new.txt"))
        with open(os.path.join(self.wt, "b.txt"), "w") as fh:
            fh.write("work\n")
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-q", "-m", "unintegrated")
        self.write_worklist()
        self.bundle()
        rc, out = self.run_script("apply")
        self.assertTrue(os.path.isdir(self.wt))
        self.assertIn("gate ancestor of origin/main: NO", out)
        self.assertIn("feat/x", git(self.repo, "branch", "--list", "feat/x"))
        # a process whose working directory is the worktree
        git(self.wt, "reset", "-q", "--hard", "origin/main")
        self.write_worklist()
        self.bundle()
        holder = subprocess.Popen(["/bin/sleep", "60"], cwd=self.wt)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        rc, out = self.run_script("apply")
        self.assertTrue(os.path.isdir(self.wt), out)
        self.assertIn("gate lsof:", out)
        self.assertNotIn("all gates pass", out)

    def test_a_missing_backup_bundle_blocks(self):
        rc, out = self.run_script("apply")  # no bundle at all
        self.assertTrue(os.path.isdir(self.wt), out)
        self.assertIn("DIFFERS(none)", out)

    def test_a_backup_bundle_that_is_older_than_the_branch_blocks(self):
        self.bundle()
        with open(os.path.join(self.wt, "b.txt"), "w") as fh:
            fh.write("more\n")
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-q", "-m", "after the backup")
        git(self.repo, "push", "-q", "origin", "feat/x:main")  # integrated, so only the bundle is stale
        git(self.repo, "fetch", "-q", "origin")
        self.write_worklist()
        rc, out = self.run_script("apply")
        self.assertTrue(os.path.isdir(self.wt), out)
        self.assertIn("bundle=DIFFERS", out)
        self.assertNotIn("all gates pass", out)

    def test_a_worktree_named_in_the_vscode_storage_file_is_skipped(self):
        self.bundle()
        with open(self.storage, "w") as fh:
            fh.write('{"windows": [{"folder": "file://%s"}]}\n' % self.wt)
        rc, out = self.run_script("apply")
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.isdir(self.wt), out)
        self.assertIn("gate storage.json matches: 1", out)
        self.assertNotIn("all gates pass", out)

    def test_a_process_that_names_the_worktree_on_its_command_line_is_skipped(self):
        self.bundle()
        holder = subprocess.Popen(["/bin/sh", "-c", "sleep 60; :", "marker", self.wt],  # not its cwd, no open file: only `ps` sees it
                                  start_new_session=True)
        self.addCleanup(holder.wait)
        self.addCleanup(os.killpg, holder.pid, 9)  # the shell and its `sleep` child
        rc, out = self.run_script("apply")
        self.assertTrue(os.path.isdir(self.wt), out)
        self.assertRegex(out, r"gate lsof: none\s+ps: +\d+ +\d+ +/bin/sh -c sleep 60; : marker")
        self.assertNotIn("all gates pass", out)

    def test_a_process_with_the_worktree_as_its_working_directory_is_seen_by_lsof(self):
        self.bundle()
        holder = subprocess.Popen(["/bin/sleep", "60"], cwd=self.wt)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        rc, out = self.run_script("apply")
        self.assertTrue(os.path.isdir(self.wt), out)
        self.assertNotIn("cannot tell", out)  # lsof worked: this is a detection, not the fail-closed path
        self.assertRegex(out, r"gate lsof: \d+\t")  # a row of the open-file list: pid, command, fd, name
        self.assertNotIn("all gates pass", out)

    def test_an_ignored_entry_outside_the_allowlist_is_skipped_and_an_allowlisted_one_is_not(self):
        self.bundle()
        os.makedirs(os.path.join(self.wt, "__pycache__"))
        with open(os.path.join(self.wt, "__pycache__", "x.pyc"), "w") as fh:
            fh.write("x\n")
        rc, out = self.run_script("dry")
        self.assertIn("all gates pass", out)  # an allowlisted generated path alone does not block
        with open(os.path.join(self.wt, "debug.log"), "w") as fh:  # ignored by .gitignore, not on the allowlist
            fh.write("secret-ish\n")
        rc, out = self.run_script("apply")
        self.assertTrue(os.path.isdir(self.wt), out)
        self.assertIn("not allowlisted: !! debug.log", out)
        self.assertNotIn("all gates pass", out)

    # --- a failing process table or open-file listing must never read as "no process uses the worktree" ---

    def test_ps_that_exits_non_zero_skips_the_item_with_the_reason(self):
        self.bundle()
        bindir = self.stub_bin("ps", 'echo "ps: boom" >&2; exit 1')
        rc, out = self.run_script("apply", {"PATH": bindir})
        self.assertEqual(rc, 0, out)
        self.assert_skipped_with_reason(out, "ps")

    def test_ps_that_prints_nothing_skips_the_item_with_the_reason(self):
        self.bundle()
        bindir = self.stub_bin("ps", "exit 0")
        rc, out = self.run_script("apply", {"PATH": bindir})
        self.assert_skipped_with_reason(out, "ps")

    def test_ps_that_cannot_be_run_skips_the_item_with_the_reason(self):
        self.bundle()
        bindir = self.stub_bin("ps", None)
        rc, out = self.run_script("apply", {"PATH": bindir})
        self.assert_skipped_with_reason(out, "ps")

    def test_lsof_that_exits_non_zero_skips_the_item_with_the_reason(self):
        self.bundle()
        bindir = self.stub_bin("lsof", 'echo "lsof: boom" >&2; exit 1')
        rc, out = self.run_script("apply", {"PATH": bindir})
        self.assertEqual(rc, 0, out)
        self.assert_skipped_with_reason(out, "lsof")

    def test_lsof_that_prints_nothing_skips_the_item_with_the_reason(self):
        self.bundle()
        bindir = self.stub_bin("lsof", "exit 0")
        rc, out = self.run_script("apply", {"PATH": bindir})
        self.assert_skipped_with_reason(out, "lsof")

    def test_lsof_that_cannot_be_run_skips_the_item_with_the_reason(self):
        self.bundle()
        bindir = self.stub_bin("lsof", None)
        rc, out = self.run_script("apply", {"PATH": bindir})
        self.assert_skipped_with_reason(out, "lsof")

    def test_a_dry_run_with_a_failing_ps_lists_no_planned_command_for_the_item(self):
        self.bundle()
        bindir = self.stub_bin("ps", "exit 1")
        rc, out = self.run_script("dry", {"PATH": bindir})
        self.assertEqual(rc, 0, out)
        self.assertNotIn("worktree remove %s" % self.wt, out)
        self.assertIn("ps unusable", out)

    def test_a_ps_stub_that_wraps_the_real_ps_does_not_change_the_result(self):
        # a stub that only wraps the real `ps`: the same item passes, so the skips above are due to the failure
        self.bundle()
        bindir = self.stub_bin("ps", 'exec %s "$@"' % shutil.which("ps"))
        rc, out = self.run_script("dry", {"PATH": bindir})
        self.assertIn("all gates pass", out)

    def test_a_path_outside_the_scratch_prefix_stops_the_run(self):
        self.bundle()
        rc, out = self.run_script("apply", {"TT_SCRATCH_PREFIX": os.path.join(self.t, "elsewhere") + "/"})
        self.assertEqual(rc, 1, out)
        self.assertIn("outside the scratch area", out)
        self.assertTrue(os.path.isdir(self.wt))

    def test_a_malformed_worklist_item_stops_the_run_before_any_gate_runs(self):
        self.bundle()
        for line in ("r1|%s||feat/x" % self.suffix, "r1|%s|abc|feat/x" % self.suffix, "r1||1234567|feat/x",
                     "|%s|1234567|feat/x" % self.suffix, "r1|%s|1234567|feat/x|extra" % self.suffix, "r1|%s|1234567" % self.suffix):
            with self.subTest(line=line):
                with open(self.work, "w") as fh:
                    fh.write(line + "\n")
                rc, out = self.run_script("apply")
                self.assertEqual(rc, 1, out)
                self.assertIn("malformed worklist item", out)
                self.assertTrue(os.path.isdir(self.wt))

    def test_an_empty_or_comment_only_worklist_and_a_missing_storage_file_stop_the_run(self):
        self.bundle()
        with open(self.work, "w") as fh:
            fh.write("# nothing\n\n")
        rc, out = self.run_script("apply")
        self.assertEqual(rc, 2, out)
        self.assertIn("no items", out)
        self.write_worklist()
        rc, out = self.run_script("apply", {"TT_VSCODE_STORAGE": os.path.join(self.t, "absent.json")})
        self.assertEqual(rc, 1, out)
        self.assertIn("storage file does not exist", out)
        self.assertTrue(os.path.isdir(self.wt))

    def test_an_unexpected_head_or_branch_stops_the_run(self):
        self.bundle()
        with open(self.work, "w") as fh:
            fh.write("r1|%s|0000000|feat/x\n" % self.suffix)
        rc, out = self.run_script("apply")
        self.assertEqual(rc, 1, out)
        self.assertIn("!= expected", out)
        self.assertTrue(os.path.isdir(self.wt))


if __name__ == "__main__":
    unittest.main()
