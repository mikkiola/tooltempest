"""The checker's own Git calls must not run a program that the user's Git configuration names.

Git runs the program of `core.fsmonitor` when it reads the index (`ls-files --stage`, which
`--tree index` needs), and with `core.fsmonitor=true` it starts a daemon that writes into the
repository's Git directory. `scripts/docops_core/gitstate.py` therefore passes
`-c core.fsmonitor=false` on every Git call.

Each case first shows, with the same configuration and plain Git, that the trap fires (a
positive control), and only then that the checker leaves it silent and the repository, its index,
configuration and refs unchanged. The setting reaches Git in five ways: the repository's own
config file, the user's global config file (`GIT_CONFIG_GLOBAL` points at a file in the test's
temporary directory), `git -c` (an outer Git passes it down through the environment), and the
`GIT_CONFIG_COUNT` and `GIT_CONFIG_PARAMETERS` environment variables. The system config file
is not exercised; it ranks below `-c` in the same way as the global one.

Scope: this covers the Git calls the checker makes itself. It does not cover the Git hook flow
around it (a pre-commit or pre-push hook that runs the checker), nor environment variables that
make Git write elsewhere (`GIT_TRACE*`).
"""
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

from helpers import DOCOPS_PY, GOOD_SPEC, DocopsTestCase, GitFixture, clean_env, run_cli
from test_nomutation_matrix import Watch, hits

TRAP = "fsmonitor"
FORMS = ("repository config", "global config", "git -c", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS")


class ConfiguredProgramsTests(DocopsTestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="docops-gitprog-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home, self.marker, self.cwd = (os.path.join(self.tmp, n) for n in ("home", "marker", "cwd"))
        for path in (self.home, self.marker, self.cwd):
            os.mkdir(path)
        self.fx = GitFixture()
        self.addCleanup(self.fx.cleanup)
        self.fx.commit_files({"SPEC.md": GOOD_SPEC, "README.md": "r\n"}, "zero")
        self.fx.stage("SPEC.md", GOOD_SPEC.replace("first", "second"))  # a staged change: the index differs from HEAD
        self.script = os.path.join(self.tmp, "fsmonitor.sh")
        with open(self.script, "w") as handle:
            handle.write("#!/bin/sh\necho %s >> %s/hit.%s.$$\n" % (TRAP, self.marker, TRAP))
        os.chmod(self.script, 0o755)
        self.report_path = os.path.join(self.tmp, "impact.json")
        with open(self.report_path, "w") as handle:
            handle.write("{}")

    def tearDown(self):
        # A daemon that a control started would keep a process and a socket alive.
        subprocess.run(["git", "-C", self.fx.root, "fsmonitor--daemon", "stop"], capture_output=True,
                       env=clean_env({"HOME": self.home}), check=False)

    # -- the four ways the setting reaches Git -------------------------------------------------
    def configure(self, form, value):
        """Return (extra environment, extra `git` arguments) that deliver core.fsmonitor=value in `form`."""
        self.fx.git("config", "--unset-all", "core.fsmonitor", check=False)  # one delivery at a time
        if form == "repository config":
            self.fx.git("config", "core.fsmonitor", value)
            return {}, []
        if form == "global config":
            path = os.path.join(self.tmp, "global.gitconfig")
            with open(path, "w") as handle:
                handle.write("[core]\n\tfsmonitor = %s\n" % value)
            return {"GIT_CONFIG_GLOBAL": path}, []
        if form == "git -c":
            return {}, ["-c", "core.fsmonitor=%s" % value]
        if form == "GIT_CONFIG_COUNT":
            return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.fsmonitor", "GIT_CONFIG_VALUE_0": value}, []
        if form == "GIT_CONFIG_PARAMETERS":
            return {"GIT_CONFIG_PARAMETERS": "'core.fsmonitor=%s'" % value}, []
        raise AssertionError(form)

    def env(self, extra):
        return clean_env({"HOME": self.home, "TMPDIR": self.tmp, **extra})

    def plain_git(self, env_extra, git_args, *args):
        return subprocess.run(["git", "-C", self.fx.root, *git_args, *args], capture_output=True,
                              env=self.env(env_extra), check=False)

    def clear_marker(self):
        for name in os.listdir(self.marker):
            os.unlink(os.path.join(self.marker, name))

    def run_docops(self, form_env, git_args, command, args):
        """The checker as the user would run it under `form`; for `git -c` through an outer Git alias,
        which is how a `-c` setting reaches a hook-run checker (in GIT_CONFIG_PARAMETERS)."""
        if git_args:
            inner = " ".join(shlex.quote(part) for part in
                             (sys.executable, "-I", str(DOCOPS_PY), command, *args, "--repo", self.fx.root))
            proc = subprocess.run(["git", "-C", self.cwd, *git_args, "-c", "alias.dd=!" + inner, "dd"],
                                  capture_output=True, env=self.env(form_env), check=False)
            return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")
        return run_cli([command, *args, "--repo", self.fx.root], cwd=self.cwd, env=self.env(form_env))

    # -- the daemon form ---------------------------------------------------------------------
    def daemon_watching(self):
        proc = self.plain_git({}, [], "fsmonitor--daemon", "status")
        return proc.returncode == 0

    # -- cases -------------------------------------------------------------------------------
    MODES = (
        ("check", ["--tree", "index"]),
        ("report", ["--tree", "index"]),
        ("check", ["--tree", "index", "--tree", "HEAD"]),
        ("check", ["--tree", "index", "--impact-file", "REPORT"]),
        ("report", ["--tree", "index", "--impact-file", "REPORT"]),
    )
    SILENT_CONTROLS = (
        ("check", ["--tree", "HEAD"]),
        ("report", ["--tree", "HEAD"]),
        ("check", ["--tree", "HEAD", "--impact-file", "REPORT"]),
    )

    def test_a_configured_fsmonitor_program_is_not_run_in_any_mode_or_form(self):
        for form in FORMS:
            with self.subTest(form=form):
                form_env, git_args = self.configure(form, self.script)
                self.clear_marker()
                self.plain_git(form_env, git_args, "ls-files", "--stage")
                self.assertEqual(hits(self.marker), [TRAP],
                                 "positive control: plain Git must run the program under %s" % form)
                for command, args in self.MODES + self.SILENT_CONTROLS:
                    args = [self.report_path if a == "REPORT" else a for a in args]
                    with self.subTest(form=form, command=command, args=args):
                        self.clear_marker()
                        watch = Watch([self.fx.root, self.home], [self.fx.root])
                        code, out, err = self.run_docops(form_env, git_args, command, args)
                        self.assertIn(code, (0, 1), "no verdict: exit %s %r" % (code, err[-200:]))
                        self.assertEqual(os.listdir(self.marker), [], "the configured program ran")
                        self.assertEqual(watch.diff(), [], "files, index, config or refs changed")
                        self.assertEqual(os.listdir(self.home), [])

    def test_the_builtin_daemon_is_not_started_in_any_form(self):
        build = subprocess.run(["git", "version", "--build-options"], capture_output=True, text=True).stdout
        if "fsmonitor--daemon" not in build:
            self.skipTest("N/A: this Git is built without the built-in file system monitor")
        for form in FORMS:
            with self.subTest(form=form):
                form_env, git_args = self.configure(form, "true")
                self.plain_git({}, [], "fsmonitor--daemon", "stop")
                self.plain_git(form_env, git_args, "ls-files", "--stage")
                self.assertTrue(self.daemon_watching(),
                                "positive control: plain Git must start the daemon under %s" % form)
                self.plain_git({}, [], "fsmonitor--daemon", "stop")
                self.assertFalse(self.daemon_watching(), "teardown of the control failed")
                watch = Watch([self.fx.root, self.home], [self.fx.root])
                code, out, err = self.run_docops(form_env, git_args, "check", ["--tree", "index"])
                self.assertEqual(code, 0, err[-200:])
                self.assertFalse(self.daemon_watching(), "the checker started the file system monitor daemon")
                self.assertEqual(watch.diff(), [], "files, index, config or refs changed")

    def test_the_override_outranks_every_delivery_form(self):
        """The premise of the fix, probed with plain Git: `-c core.fsmonitor=false` wins over the
        global config file and over both environment forms (the repository file is covered by the
        checker-level test above)."""
        for form in ("global config", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS"):
            with self.subTest(form=form):
                form_env, _ = self.configure(form, self.script)
                self.clear_marker()
                self.plain_git(form_env, [], "ls-files", "--stage")
                self.assertEqual(hits(self.marker), [TRAP], "positive control")
                self.clear_marker()
                self.plain_git(form_env, ["-c", "core.fsmonitor=false"], "ls-files", "--stage")
                self.assertEqual(os.listdir(self.marker), [], "-c core.fsmonitor=false did not win")


if __name__ == "__main__":
    unittest.main()
