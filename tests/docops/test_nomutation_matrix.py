"""`docops.py check` and `report` change nothing they inspect, over a defined input matrix.

Method. A differential test: take a snapshot of everything that could be touched,
run the command, take the snapshot again, and require the two to be equal. The
snapshot is taken for every entry under each protected root, `.git` included:
type, permission bits, size, modification time, inode number and a SHA-256 of the
content (the target for a symlink); for directories type, permission bits and
modification time, so that even a lock file that is created and removed again is
seen (it changes its directory's modification time). The roots are the inspected
repository (and, for a linked worktree or a bare clone, the repository it belongs
to), the impact-report directory, the working directory of the run, a throwaway
HOME and TMPDIR (which must stay empty), a directory that traps repository-configured
commands (which must stay empty), and the real `scripts/` directory of this checkout
(no bytecode). The Git-side views are named so that a failure says what changed: the
index file, HEAD, the ref files and packed refs, the configuration, the object store.

Why this method and not mutation testing of the checker: the property is "no observable
write over a set of inputs", which is what a before/after comparison measures. Mutation
testing answers a different question, whether these tests are sensitive, and was used for
that. The author's record of that check: thirteen writes were injected into the checker's
source one at a time (index refresh on open and on read, a configuration key, a file created
in the repository, a mode change, a modification-time change, a ref, an object, a file in
HOME, a file in TMPDIR, a transient index.lock, an append to and a deletion of the impact
report) and this file caught every one; the mutants are not kept in the repository, so that
run can be repeated but is not reproduced by the suite. `WatchSelfTests` shows, inside the
suite, that the snapshot can see each class of write on its own.

Input classes covered (each runs through `check` and `report`; the exit code must be
the expected one, so the run really took the path named):
  valid snapshots      index, HEAD, a branch, an annotated and a lightweight tag, a commit id,
                       several trees at once, an idle SPEC, a SPEC where applicability cannot
                       be established, a UTF-8 BOM with CRLF line endings; with a stat-dirty
                       index, staged, unstaged and untracked changes and an ignored file present
  malformed SPECs      failing structure, unterminated frontmatter, invalid UTF-8, an empty file
  push input           good, failing, mixed, deleted ref, malformed lines, empty stdin
  impact reports       a valid minimal and a valid full report (the Git-reading validation
                       paths), empty object, malformed, missing, a directory, a symlink into the
                       repository, one over the size limit, standard input, inside and outside
                       the repository
  error paths          unknown ref, a blob instead of a commit, usage errors, a missing path, a
                       plain directory, a file as the repository, git not found
  repository shapes    a bare clone, a linked worktree, a SHA-256 repository, an alternate
                       GIT_INDEX_FILE (as in a hook), a stale index.lock, a subdirectory as the
                       working directory with the default --repo
  hostile configuration  repository-local external diff, textconv, clean and smudge
                       filters and hooks that would write a marker file if Git ran them. A
                       positive control shows that the external diff, textconv, clean and smudge
                       settings and the pre-commit hook each fire under a Git command that does run
                       them (the other installed hooks are not controlled), so "not executed by
                       the checker" means something for those.
Limits, stated plainly. This tests those classes with the installed Git and Python, not
every possible input. It cannot see: a write that restores identical bytes with an unchanged
modification time and inode; access times, ctime, ownership, extended attributes or ACLs;
a subdirectory it cannot read (os.walk skips it); a same-second rewrite on a file system
with one-second timestamps; anything written outside the protected roots; a concurrent
writer would look like a failure. Git's own `core.fsmonitor` program is not a trap that
stays silent: Git runs it when the checker reads the index (`--tree index`) and the checker
neither starts nor sandboxes it; that case is pinned by its own test. Pager, editor and
askpass settings are not exercised at all: they act only on interactive Git use (a terminal on
standard output, an editor or a credential prompt), which a read-only checker never produces.
"""
import hashlib
import json
import os
import shutil
import stat
import tempfile
import unittest

from helpers import (GOOD_SPEC, IDLE_SPEC, SCRIPTS_DIR, DocopsTestCase, GitFixture, clean_env, run_cli)

from docops_core import impact
from test_impact import full_report, minimal_report, review

BAD_SPEC = "# Broken\n\n## Goals\n\n- [ ] only a goal\n"
ZERO = "0" * 40
TRAP_KEYS = ("external-diff", "textconv", "clean", "smudge")


def entry_state(path):
    info = os.lstat(path)
    kind = stat.S_IFMT(info.st_mode)
    if stat.S_ISDIR(info.st_mode):
        return (kind, stat.S_IMODE(info.st_mode), info.st_mtime_ns)
    content = None
    if stat.S_ISLNK(info.st_mode):
        content = os.readlink(path)
    elif stat.S_ISREG(info.st_mode):
        with open(path, "rb") as handle:
            content = hashlib.sha256(handle.read()).hexdigest()
    return (kind, stat.S_IMODE(info.st_mode), info.st_size, info.st_mtime_ns, info.st_ino, content)


def fs_state(root):
    """Everything under `root` without following symlinks (the root itself included)."""
    state = {".": entry_state(root)}
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        for name in dirs + sorted(files):
            path = os.path.join(base, name)
            state[os.path.relpath(path, root)] = entry_state(path)
    return state


def git_views(repo_root):
    """Named Git-side views, read straight from the files, never through a Git command that could write."""
    git_dir = os.path.join(repo_root, ".git")
    if os.path.isfile(git_dir):
        with open(git_dir) as handle:
            git_dir = os.path.join(repo_root, handle.read().split("gitdir:", 1)[1].strip())
    elif not os.path.isdir(git_dir):
        git_dir = repo_root  # a bare repository

    def blob(name):
        path = os.path.join(git_dir, name)
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    refs = {}
    for base, _, files in os.walk(os.path.join(git_dir, "refs")):
        for name in files:
            rel = os.path.relpath(os.path.join(base, name), git_dir)
            refs[rel] = blob(rel)
    objects = {}
    for base, _, files in os.walk(os.path.join(git_dir, "objects")):
        for name in files:
            objects[os.path.relpath(os.path.join(base, name), git_dir)] = os.lstat(os.path.join(base, name)).st_size
    return {"index": blob("index"), "HEAD": blob("HEAD"), "config": blob("config"),
            "packed-refs": blob("packed-refs"), "refs": refs, "objects": objects}


def describe(before, after):
    """Short, readable list of what differs between two state dictionaries."""
    changed = []
    for key in sorted(set(before) | set(after)):
        if before.get(key) != after.get(key):
            if key not in before:
                changed.append("%s: appeared" % key)
            elif key not in after:
                changed.append("%s: disappeared" % key)
            else:
                changed.append("%s: changed" % key)
    return changed[:12]


class Watch:
    """The protected roots of one run and their state before it."""

    def __init__(self, roots, views_of=()):
        self.roots = list(roots)
        self.views_of = list(views_of)
        self.before = self.take()

    def take(self):
        return ({root: fs_state(root) for root in self.roots},
                {repo: git_views(repo) for repo in self.views_of})

    def diff(self):
        after = self.take()
        problems = []
        for root in self.roots:
            problems += ["%s: %s" % (root, line) for line in describe(self.before[0][root], after[0][root])]
        for repo in self.views_of:
            for name, view in self.before[1][repo].items():
                if view != after[1][repo][name]:
                    problems.append("%s: git %s changed" % (repo, name))
        return problems


def install_traps(fx, tmp, marker):
    """Repository-local settings that write a tagged file into `marker` if Git runs them.
    (core.fsmonitor is not among them: Git does run it for `--tree index`; see its own test.)"""
    def script(tag):
        path = os.path.join(tmp, "trap-%s.sh" % tag)
        with open(path, "w") as handle:
            # filters read standard input and must pass it through; the others must not wait on it
            passthrough = "cat\n" if tag in ("clean", "smudge") else ""
            handle.write("#!/bin/sh\necho %s >> %s/hit.%s.$$\n%s" % (tag, marker, tag, passthrough))
        os.chmod(path, 0o755)
        return path

    fx.git("config", "diff.external", script("external-diff"))
    fx.git("config", "diff.trap.textconv", script("textconv"))
    fx.git("config", "filter.trap.clean", script("clean"))
    fx.git("config", "filter.trap.smudge", script("smudge"))
    hooks = os.path.join(tmp, "hooks")
    os.makedirs(hooks, exist_ok=True)
    for name in ("pre-commit", "post-checkout", "post-merge", "reference-transaction", "pre-push", "post-commit"):
        path = os.path.join(hooks, name)
        with open(path, "w") as handle:
            handle.write("#!/bin/sh\necho hook-%s >> %s/hit.hook-%s.$$\n" % (name, marker, name))
        os.chmod(path, 0o755)
    fx.git("config", "core.hooksPath", hooks)
    os.makedirs(os.path.join(fx.root, ".git", "info"), exist_ok=True)
    with open(os.path.join(fx.root, ".git", "info", "attributes"), "w") as handle:
        handle.write("*.py filter=trap diff=trap\n")


def hits(marker):
    return sorted({name.split(".")[1] for name in os.listdir(marker) if name.startswith("hit.")})


class NoMutationMatrix(DocopsTestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = os.path.realpath(tempfile.mkdtemp(prefix="docops-nomut-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        cls.home, cls.tmpdir, cls.marker, cls.reports, cls.cwd = (os.path.join(cls.tmp, n) for n in (
            "home", "tmpdir", "marker", "reports", "cwd"))
        for path in (cls.home, cls.tmpdir, cls.marker, cls.reports, cls.cwd):
            os.mkdir(path)
        cls.fx = GitFixture()
        cls.addClassCleanup(cls.fx.cleanup)
        cls.build(cls.fx)

    # -- the repository under inspection -------------------------------------------------------
    @classmethod
    def build(cls, fx):
        fx.commit_files({"SPEC.md": GOOD_SPEC, "README.md": "r\n"}, "zero")
        fx.commit_files({"code.py": "x = 1\n", "a b/notes.txt": "spaces in a name\n",
                         "docs/ünï.md": "unicode name\n", ".gitignore": "ignored.log\n"}, "one")
        cls.commits = {"main": fx.git("rev-parse", "HEAD")}
        cls.trees = {"base": fx.git("rev-parse", "HEAD~1^{tree}"), "subject": fx.git("rev-parse", "HEAD^{tree}")}
        cls.changed = fx.git("-c", "core.quotepath=false", "diff-tree", "-r", "--name-only", "HEAD~1", "HEAD").splitlines()
        fx.git("switch", "-q", "-c", "work")
        cls.commits["feature"] = fx.commit_files({"SPEC.md": BAD_SPEC}, "failing structure")
        cls.commits["frontmatter"] = fx.commit_files(
            {"SPEC.md": "---\nspec-state: idle\n# never closed\n" + GOOD_SPEC}, "unterminated frontmatter")
        fx.write("SPEC.md", b"\xff\xfe\x00 not utf-8 \x80\n")
        fx.git("add", "SPEC.md")
        cls.commits["invalid-utf8"] = fx.commit("invalid utf-8")
        cls.commits["empty"] = fx.commit_files({"SPEC.md": ""}, "empty spec")
        cls.commits["bom-crlf"] = fx.commit_files(
            {"SPEC.md": (b"\xef\xbb\xbf" + GOOD_SPEC.replace("\n", "\r\n").encode())}, "bom and crlf")
        cls.commits["idle"] = fx.commit_files({"SPEC.md": IDLE_SPEC}, "idle")
        fx.git("rm", "-q", "SPEC.md")
        fx.stage("deep/er/SPEC.md", GOOD_SPEC)
        cls.commits["nested"] = fx.commit("nested only")
        fx.git("switch", "-q", "main")
        fx.git("branch", "-f", "feature", cls.commits["feature"])  # the branch names the failing commit
        fx.git("tag", "-a", "-m", "annotated", "v1", cls.commits["feature"])
        fx.git("tag", "light")
        cls.commits["good"] = cls.commits["main"]
        fx.stage("SPEC.md", GOOD_SPEC.replace("first", "second"))  # staged change
        fx.write("code.py", "x = 2\n")                              # unstaged change
        fx.write("untracked.txt", "u\n")
        fx.write("ignored.log", "ignored\n")
        os.utime(os.path.join(fx.root, "SPEC.md"), None)             # the index entry is now stat-dirty
        cls.blob_oid = fx.git("rev-parse", "main:code.py")
        install_traps(fx, cls.tmp, cls.marker)

    # -- running -------------------------------------------------------------------------------
    def env(self, extra=None):
        base = {"HOME": self.home, "TMPDIR": self.tmpdir, "TMP": self.tmpdir, "TEMP": self.tmpdir}
        base.update(extra or {})
        return clean_env(base)

    def protected(self, *extra_roots):
        return [self.fx.root, self.reports, self.cwd, self.home, self.tmpdir, self.marker, str(SCRIPTS_DIR),
                *extra_roots]

    def matrix_run(self, name, args, expected, stdin=b"", repo="default", cwd=None, env_extra=None,
                   extra_roots=(), views=()):
        """Run `check` and `report` with the same arguments; require the expected exit code and no change."""
        repo_args = [] if repo is None else ["--repo", self.fx.root if repo == "default" else repo]
        for hit in os.listdir(self.marker):  # earlier fixture steps (clone, worktree add) ran their hooks
            os.unlink(os.path.join(self.marker, hit))
        watch = Watch(self.protected(*extra_roots), [self.fx.root, *views])
        for command in ("check", "report"):
            code, out, err = run_cli([command, *repo_args, *args], cwd=cwd or self.cwd, stdin=stdin,
                                     env=self.env(env_extra))
            self.assertEqual(code, expected, "%s via %s: exit %s, out=%r err=%r" % (name, command, code, out[-200:], err[-200:]))
            if command == "check" and out.strip().startswith("{"):
                self.assertValidResult(json.loads(out))
            self.assertEqual(watch.diff(), [], "%s via %s changed what it inspected" % (name, command))
        self.assertEqual(os.listdir(self.marker), [], "%s: a repository-configured command was executed" % name)
        self.assertEqual(os.listdir(self.home), [], "%s: wrote into HOME" % name)
        self.assertEqual(os.listdir(self.tmpdir), [], "%s: wrote into TMPDIR" % name)

    def update(self, local_ref, oid, remote_ref="refs/heads/main", remote_oid=ZERO):
        return "%s %s %s %s\n" % (local_ref, oid, remote_ref, remote_oid)

    def report_file(self, name, content, directory=None):
        path = os.path.join(directory or self.reports, name)
        with open(path, "wb") as handle:
            handle.write(content if isinstance(content, bytes) else content.encode())
        self.addCleanup(lambda: os.path.lexists(path) and os.unlink(path))
        return path

    # -- valid snapshots -------------------------------------------------------------------------
    def test_valid_snapshots(self):
        c = self.commits
        cases = [
            ("index (staged, unstaged, untracked, ignored and stat-dirty state present)", ["--tree", "index"], 0),
            ("HEAD", ["--tree", "HEAD"], 0),
            ("branch name", ["--tree", "feature"], 1),
            ("annotated tag", ["--tree", "v1"], 1),
            ("lightweight tag", ["--tree", "light"], 0),
            ("commit id", ["--tree", c["good"]], 0),
            ("several trees", ["--tree", "HEAD", "--tree", "feature", "--tree", "index"], 1),
            ("idle SPEC", ["--tree", c["idle"]], 0),
            ("applicability not established", ["--tree", c["nested"]], 3),
            ("UTF-8 BOM with CRLF line endings", ["--tree", c["bom-crlf"]], 0),
        ]
        for name, args, expected in cases:
            with self.subTest(name):
                self.matrix_run(name, args, expected)

    def test_malformed_specs(self):
        for name in ("frontmatter", "invalid-utf8", "empty"):
            with self.subTest(name):
                self.matrix_run(name, ["--tree", self.commits[name]], 1)

    # -- push input --------------------------------------------------------------------------------
    def test_push_input(self):
        c = self.commits
        good = self.update("refs/heads/main", c["good"])
        bad = self.update("refs/heads/feature", c["feature"])
        deleted = self.update("(delete)", ZERO, "refs/heads/gone", c["good"])
        cases = [
            ("good ref", good, 0), ("failing ref", bad, 1), ("mixed", good + bad, 1), ("deleted ref only", deleted, 0),
            ("good and deleted", good + deleted, 0),
            ("malformed line", "refs/heads/x not-an-oid refs/heads/x %s\n" % ZERO, 2),
            ("wrong field count", "just three fields here\n", 2),
            ("empty stdin", "", 2), ("blank lines only", "\n\n", 2),
            ("unknown oid", self.update("refs/heads/x", "1" * 40), 2),
            ("crlf lines", good.replace("\n", "\r\n"), 0),
        ]
        for name, stdin, expected in cases:
            with self.subTest(name):
                self.matrix_run(name, ["--push-stdin"], expected, stdin=stdin.encode())

    # -- impact reports ----------------------------------------------------------------------------------
    def test_valid_impact_reports_reach_the_git_reading_validation_paths(self):
        subject = self.trees["subject"]
        minimal = minimal_report(self.trees["base"], subject, self.changed, evidence=[review("e1", subject)])
        full = full_report(self.trees["base"], subject, {"architecture": self.changed},
                           evidence=[review("e1", subject)])
        for name, report in (("minimal", minimal), ("full", full)):
            outside = self.report_file("valid-%s.json" % name, json.dumps(report))
            inside = self.report_file("valid-%s.json" % name, json.dumps(report), directory=self.fx.root)
            for label, where in (("outside the repository", outside), ("inside the repository", inside)):
                with self.subTest("%s report, %s" % (name, label)):
                    self.matrix_run("valid %s report" % name, ["--tree", self.commits["good"], "--impact-file", where], 0)
            with self.subTest("%s report on standard input" % name):
                self.matrix_run("valid %s report on stdin" % name,
                                ["--tree", self.commits["good"], "--impact-file", "-"], 0,
                                stdin=json.dumps(report).encode())
        # sanity: the report really reached the assessment (a base tree that does not exist is a FAIL of it)
        wrong = minimal_report("9" * 40, subject, self.changed, evidence=[review("e1", subject)])
        with self.subTest("a report naming a base tree that does not exist"):
            path = self.report_file("wrong-base.json", json.dumps(wrong))
            self.matrix_run("wrong base tree", ["--tree", self.commits["good"], "--impact-file", path], 1)

    def test_other_impact_report_inputs(self):
        good = self.commits["good"]
        outside = self.report_file("outside.json", "{}")
        inside = self.report_file("inside.json", "{}", directory=self.fx.root)
        malformed = self.report_file("malformed.json", "{not json")
        oversized = self.report_file("big.json", b" " * (impact.MAX_REPORT_BYTES + 5))
        link = os.path.join(self.reports, "link.json")
        os.symlink(os.path.join(self.fx.root, "SPEC.md"), link)
        self.addCleanup(lambda: os.path.lexists(link) and os.unlink(link))
        directory = os.path.join(self.reports, "adir")
        os.mkdir(directory)
        self.addCleanup(lambda: os.path.isdir(directory) and os.rmdir(directory))
        base = ["--tree", good, "--impact-file"]
        cases = [
            ("empty object inside the repository", base + [inside], 1),
            ("empty object outside the repository", base + [outside], 1),
            ("malformed report", base + [malformed], 1),
            ("missing report", base + [os.path.join(self.reports, "missing.json")], 2),
            ("report is a directory", base + [directory], 2),
            ("report is a symlink into the repository", base + [link], 1),
            ("report over the size limit", base + [oversized], 1),
        ]
        for name, args, expected in cases:
            with self.subTest(name):
                self.matrix_run(name, args, expected)
        with self.subTest("empty object on standard input"):
            self.matrix_run("report on stdin", base + ["-"], 1, stdin=b"{}")
        with self.subTest("standard input claimed twice"):
            self.matrix_run("impact on stdin and push-stdin", ["--push-stdin", "--impact-file", "-"], 2, stdin=b"{}")

    # -- error paths ---------------------------------------------------------------------------------------
    def test_error_paths(self):
        with tempfile.TemporaryDirectory() as plain:
            path_to_file = os.path.join(self.tmp, "a-file")
            with open(path_to_file, "w") as handle:
                handle.write("x")
            self.addCleanup(lambda: os.path.exists(path_to_file) and os.unlink(path_to_file))
            cases = [
                ("unknown ref", ["--tree", "no-such-ref"], 2, {}),
                ("a blob, not a commit", ["--tree", self.blob_oid], 2, {}),
                ("usage: no tree", [], 2, {}),
                ("usage: bogus option", ["--tree", "HEAD", "--bogus"], 2, {}),
                ("tree without a value", ["--tree"], 2, {}),
                ("path that does not exist", ["--tree", "index"], 2, {"repo": os.path.join(plain, "missing")}),
                ("a plain directory", ["--tree", "index"], 2, {"repo": plain}),
                ("a file as the repository", ["--tree", "index"], 2, {"repo": path_to_file}),
                ("git not found", ["--tree", "HEAD"], 2, {"env_extra": {"PATH": plain}}),
            ]
            for name, args, expected, kwargs in cases:
                with self.subTest(name):
                    self.matrix_run(name, args, expected, **kwargs)

    # -- repository shapes -----------------------------------------------------------------------------------
    def test_bare_clone(self):
        bare = os.path.join(self.tmp, "bare.git")
        self.fx.git("clone", "-q", "--bare", self.fx.root, bare)
        self.addCleanup(shutil.rmtree, bare, True)
        self.matrix_run("bare clone, commits", ["--tree", "HEAD", "--tree", "main"], 0, repo=bare,
                        extra_roots=[bare], views=[bare])
        self.matrix_run("bare clone has no index", ["--tree", "index"], 2, repo=bare, extra_roots=[bare], views=[bare])

    def test_linked_worktree(self):
        linked = os.path.join(self.tmp, "linked")
        self.fx.git("worktree", "add", "-q", "-b", "side", linked, "main")

        def remove():
            self.fx.git("worktree", "remove", "--force", linked, check=False)
            self.fx.git("branch", "-D", "side", check=False)

        self.addCleanup(remove)
        with open(os.path.join(linked, "untracked-in-worktree.txt"), "w") as handle:
            handle.write("w\n")
        self.matrix_run("linked worktree, own index", ["--tree", "index", "--tree", "HEAD"], 0, repo=linked,
                        extra_roots=[linked], views=[linked])

    def test_sha256_repository(self):
        with GitFixture("sha256") as fx:
            fx.commit_files({"SPEC.md": GOOD_SPEC}, "good")
            good = fx.git("rev-parse", "HEAD")
            fx.write("SPEC.md", GOOD_SPEC + "\nedit\n")
            fx.git("add", "SPEC.md")
            for name, args, stdin in (("index", ["--tree", "index"], b""), ("HEAD", ["--tree", "HEAD"], b""),
                                      ("push", ["--push-stdin"], self.update("refs/heads/main", good).encode())):
                with self.subTest(name):
                    for hit in os.listdir(self.marker):
                        os.unlink(os.path.join(self.marker, hit))
                    watch = Watch(self.protected(fx.root), [fx.root])
                    for command in ("check", "report"):
                        code, out, _ = run_cli([command, "--repo", fx.root, *args], stdin=stdin, cwd=self.cwd,
                                               env=self.env())
                        self.assertEqual(code, 0, out[-200:])
                        self.assertEqual(watch.diff(), [])
                    self.assertEqual((os.listdir(self.marker), os.listdir(self.home), os.listdir(self.tmpdir)),
                                     ([], [], []))

    def test_alternate_index_file_as_in_a_hook(self):
        alt = os.path.join(self.fx.root, ".git", "alt-index")
        shutil.copy(os.path.join(self.fx.root, ".git", "index"), alt)
        self.addCleanup(lambda: os.path.exists(alt) and os.unlink(alt))
        self.matrix_run("GIT_INDEX_FILE", ["--tree", "index"], 0, env_extra={"GIT_INDEX_FILE": alt})

    def test_stale_index_lock_is_left_exactly_as_it_is(self):
        lock = os.path.join(self.fx.root, ".git", "index.lock")
        with open(lock, "w") as handle:
            handle.write("held by someone else\n")
        self.addCleanup(lambda: os.path.exists(lock) and os.unlink(lock))
        self.matrix_run("stale index.lock", ["--tree", "index", "--tree", "HEAD"], 0)
        self.assertTrue(os.path.exists(lock))

    def test_subdirectory_as_working_directory_with_the_default_repo(self):
        cwd = os.path.join(self.fx.root, "a b")
        self.matrix_run("working directory below the root, default --repo", ["--tree", "HEAD"], 0, repo=None, cwd=cwd)

    # -- configuration that would run programs -------------------------------------------------------------
    def test_every_trap_fires_under_a_git_command_that_runs_it(self):
        """Positive control for the hostile-configuration class: in a scratch repository with the
        same settings, ordinary Git commands start each trap, so a silent marker directory during
        the matrix is evidence about the checker and not about unreachable settings."""
        with tempfile.TemporaryDirectory() as scratch:
            marker = os.path.join(scratch, "marker")
            os.mkdir(marker)
            with GitFixture() as fx:
                fx.commit_files({"code.py": "x = 1\n"}, "first")
                fx.commit_files({"code.py": "x = 2\n"}, "second")
                install_traps(fx, scratch, marker)
                fx.git("diff", "HEAD~1", "HEAD")                         # external diff
                fx.git("diff", "--no-ext-diff", "--textconv", "HEAD~1", "HEAD")  # textconv
                fx.write("code.py", "x = 3\n")
                fx.git("add", "code.py")                                 # clean filter
                fx.git("checkout", "-q", "HEAD", "--", "code.py")        # smudge filter
                fx.git("commit", "-q", "--allow-empty", "-m", "third")   # hooks
            fired = hits(marker)
        for key in TRAP_KEYS:
            self.assertIn(key, fired, "%s never ran, so its silence in the matrix proves nothing" % key)
        self.assertIn("hook-pre-commit", fired)

    def test_a_configured_fsmonitor_is_run_by_git_for_the_index_and_the_repository_still_does_not_change(self):
        """Known limitation, pinned so that it is not forgotten: with `core.fsmonitor` set in the
        repository's own configuration, Git runs that program when the checker reads the index
        (`--tree index`); it does not when it reads a commit. The checker neither starts nor
        sandboxes the program. With a program that only logs, nothing in the repository changes."""
        with GitFixture() as fx:
            fx.commit_files({"SPEC.md": GOOD_SPEC})
            script = os.path.join(self.tmp, "fsmonitor.sh")
            with open(script, "w") as handle:
                handle.write("#!/bin/sh\necho \"$@\" >> %s/fsmonitor.log\n" % self.marker)
            os.chmod(script, 0o755)
            fx.git("config", "core.fsmonitor", script)
            for hit in os.listdir(self.marker):
                os.unlink(os.path.join(self.marker, hit))
            watch = Watch([fx.root, self.home, self.tmpdir], [fx.root])
            code, _, _ = run_cli(["check", "--repo", fx.root, "--tree", "HEAD"], cwd=self.cwd, env=self.env())
            self.assertEqual((code, os.listdir(self.marker)), (0, []), "reading a commit must not start it")
            code, _, _ = run_cli(["check", "--repo", fx.root, "--tree", "index"], cwd=self.cwd, env=self.env())
            self.assertEqual(code, 0)
            self.assertEqual(os.listdir(self.marker), ["fsmonitor.log"])
            os.unlink(os.path.join(self.marker, "fsmonitor.log"))
            self.assertEqual(watch.diff(), [])

    def test_the_checkout_scripts_directory_gains_no_bytecode(self):
        before = fs_state(str(SCRIPTS_DIR))
        code, _, _ = run_cli(["check", "--repo", self.fx.root, "--tree", "HEAD"], cwd=self.cwd,
                             env={k: v for k, v in self.env().items() if k != "PYTHONDONTWRITEBYTECODE"})
        self.assertEqual(code, 0)
        self.assertEqual(describe(before, fs_state(str(SCRIPTS_DIR))), [])


class WatchSelfTests(unittest.TestCase):
    """The snapshot must be able to see every kind of write it claims to see, one at a time."""

    def setUp(self):
        self.fx = GitFixture()
        self.addCleanup(self.fx.cleanup)
        self.fx.commit_files({"SPEC.md": GOOD_SPEC, "dir/f.txt": "xx\n"})
        self.watch = Watch([self.fx.root], [self.fx.root])

    def path(self, rel):
        return os.path.join(self.fx.root, rel)

    def assertSeen(self, label, watch=None):
        self.assertNotEqual((watch or self.watch).diff(), [], "a write went unseen: " + label)

    def test_unchanged_is_unchanged(self):
        self.assertEqual(self.watch.diff(), [])

    def test_content_change(self):
        with open(self.path("SPEC.md"), "a") as handle:
            handle.write("x")
        self.assertSeen("append")

    def test_content_change_with_size_and_mtime_restored_is_seen_through_the_hash(self):
        target = self.path("dir/f.txt")
        info, directory = os.stat(target), os.stat(self.path("dir"))
        with open(target, "wb") as handle:
            handle.write(b"yy\n")  # same size, different bytes
        os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
        os.utime(self.path("dir"), ns=(directory.st_atime_ns, directory.st_mtime_ns))
        after = os.stat(target)
        self.assertEqual((after.st_size, after.st_mtime_ns, after.st_ino), (info.st_size, info.st_mtime_ns, info.st_ino))
        self.assertSeen("same size, mtime and inode; other bytes")

    def test_rewriting_the_same_bytes_through_a_new_file_is_seen_through_the_inode(self):
        target = self.path("dir/f.txt")
        info, directory = os.stat(target), os.stat(self.path("dir"))
        with open(target, "rb") as handle:
            data = handle.read()
        replacement = target + ".new"
        with open(replacement, "wb") as handle:  # created while the original still exists: its inode cannot be reused
            handle.write(data)
        os.rename(replacement, target)
        os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
        os.utime(self.path("dir"), ns=(directory.st_atime_ns, directory.st_mtime_ns))
        after = os.stat(target)
        self.assertEqual((after.st_size, after.st_mtime_ns), (info.st_size, info.st_mtime_ns))
        self.assertNotEqual(after.st_ino, info.st_ino)
        self.assertSeen("same bytes, size and mtime; new inode")

    def test_new_file(self):
        open(self.path("new.txt"), "w").close()
        self.assertSeen("new file")

    def test_mode_change(self):
        os.chmod(self.path("SPEC.md"), 0o600)
        self.assertSeen("chmod")

    def test_touch(self):
        os.utime(self.path("SPEC.md"), (1, 1))
        self.assertSeen("utime")

    def test_a_lock_file_created_and_removed_again(self):
        lock = self.path(".git/index.lock")
        open(lock, "w").close()
        os.unlink(lock)
        self.assertSeen("transient lock")

    def test_a_ref_and_a_config_key(self):
        for label, change in (("ref", lambda: self.fx.git("update-ref", "refs/docops/x", "HEAD")),
                              ("config", lambda: self.fx.git("config", "docops.x", "1"))):
            watch = Watch([self.fx.root], [self.fx.root])
            change()
            self.assertSeen(label, watch)

    def test_the_index(self):
        os.utime(self.path("SPEC.md"), None)  # a stat-dirty entry, so that the refresh rewrites the index
        watch = Watch([self.fx.root], [self.fx.root])
        self.fx.git("update-index", "--really-refresh")
        self.assertTrue(any(".git/index" in p for p in watch.diff()), watch.diff())

    def test_a_new_object_is_seen_in_the_named_git_view(self):
        self.fx.write("fresh.txt", "fresh\n")
        watch = Watch([self.fx.root], [self.fx.root])
        self.fx.git("hash-object", "-w", "fresh.txt")
        self.assertTrue(any("git objects" in p for p in watch.diff()), watch.diff())

    def test_a_write_into_an_unlisted_root_is_not_seen_which_is_why_the_roots_are_listed(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            watch = Watch([self.fx.root])
            open(os.path.join(elsewhere, "x"), "w").close()
            self.assertEqual(watch.diff(), [])


if __name__ == "__main__":
    unittest.main()
