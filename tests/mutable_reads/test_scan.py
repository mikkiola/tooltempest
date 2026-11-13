"""`scripts/check_mutable_reads.py` (ADR-0022): the bounded static scan for reads of normative content or
executables from a mutable location.

Every pattern P1 to P7 has a positive fixture (the scan must report exactly that pattern) and a negative
fixture (the scan must report nothing). The incident fixtures are neutral rewrites of the shapes found in the
two incidents and in the audit that followed (a guard that imports its siblings from its own directory); they carry no real path or account name. The scan is a heuristic over lines: these tests state
what it recognizes, not that it recognizes every form. `--limits` prints the forms it does not cover.
"""
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN = REPO_ROOT / "scripts" / "check_mutable_reads.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

FINDING = re.compile(r"^(?P<path>.+):(?P<line>\d+): (?P<id>P[1-7]): ")

EXPECTED = {
    "p1_pos.py": {"P1"},
    "p1_pos_var.sh": {"P1"},
    "p1_neg.sh": set(),
    "p1_neg.py": set(),
    "p2_pos.sh": {"P2"},
    "p2_neg.sh": set(),
    "p3_pos.sh": {"P3"},
    "p3_pos.py": {"P3"},
    "p3_neg.sh": set(),
    "p3_pos_inline.sh": {"P3"},
    "p4_pos.py": {"P4"},
    "p4_neg.py": set(),
    "p5_pos.sh": {"P5"},
    "p5_neg.sh": set(),
    "p6_pos.sh": {"P6"},
    "p6_neg.sh": set(),
    "p6_neg_git.py": set(),
    "p6_pos.py": {"P6"},
    "p6_pos_multiline.py": {"P6"},
    "p6_pos_star_popen.py": {"P6"},
    "p6_pos_flag_value.py": {"P6"},
    "p6_pos_comment.py": {"P6"},
    "p6_neg_interp_flag_value.py": set(),
    "p6_limit_launcher.py": set(),
    "p6_limit_comma_in_string.py": set(),
    "p6_limit_hash_in_string.py": set(),
    "p7_limit_lowercase_name.py": set(),
    "p7_limit_constant_first_in.py": set(),
    "p1_limit_join_variable.py": set(),
    "p7_pos.py": {"P7"},
    "p7_neg.py": set(),
    "p7_pos_path_name.py": {"P7"},
    "incident_gate_like.py": {"P1", "P7"},
    "incident_precommit_like.sh": {"P3", "P5"},
    "incident_pretool_like.py": {"P4"},
}


def scan(*args, cwd=None, env=None):
    proc = subprocess.run([sys.executable, "-I", str(SCAN), *[str(a) for a in args]], capture_output=True,
                          text=True, cwd=cwd, env=env, timeout=60)
    return proc.returncode, proc.stdout, proc.stderr


def ids_of(stdout):
    return {m.group("id") for m in (FINDING.match(line) for line in stdout.splitlines()) if m}


def git(cwd, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
                          cwd=cwd, capture_output=True, text=True, env=env, check=True).stdout


class FixtureTests(unittest.TestCase):
    def test_every_fixture_is_listed_and_every_listed_fixture_exists(self):
        self.assertEqual({p.name for p in FIXTURES.iterdir()}, set(EXPECTED))

    def test_every_pattern_has_a_positive_and_a_negative_fixture(self):
        positive = set().union(*(ids for name, ids in EXPECTED.items() if "_pos" in name))
        negative = {name.split("_")[0].upper() for name, ids in EXPECTED.items() if "_neg" in name}
        self.assertEqual(positive, {"P%d" % n for n in range(1, 8)})
        self.assertEqual(negative, {"P%d" % n for n in range(1, 8)})

    def test_each_fixture_yields_exactly_its_expected_patterns(self):
        for name, wanted in sorted(EXPECTED.items()):
            with self.subTest(fixture=name):
                code, out, err = scan(FIXTURES / name)
                self.assertEqual(ids_of(out), wanted, out + err)
                self.assertEqual(code, 1 if wanted else 0, out + err)

    def test_a_finding_names_file_line_pattern_and_the_offending_text(self):
        code, out, _ = scan(FIXTURES / "p5_pos.sh")
        self.assertEqual(code, 1)
        lines = [line for line in out.splitlines() if FINDING.match(line)]
        self.assertTrue(lines)
        m = FINDING.match(lines[0])
        self.assertTrue(m.group("path").endswith("p5_pos.sh"))
        self.assertEqual(m.group("line"), "3")
        self.assertIn("dirname", lines[0])

    def test_a_directory_is_walked_and_findings_of_all_files_are_reported(self):
        code, out, _ = scan(FIXTURES)
        self.assertEqual(code, 1)
        self.assertEqual(ids_of(out), {"P%d" % n for n in range(1, 8)})

    def test_json_output_has_the_same_findings(self):
        code, out, _ = scan("--format", "json", FIXTURES / "incident_gate_like.py")
        self.assertEqual(code, 1)
        data = json.loads(out)
        self.assertEqual({f["pattern"] for f in data["findings"]}, {"P1", "P7"})
        self.assertEqual(data["hygiene"], [])
        self.assertTrue(all({"path", "line", "pattern", "message"} <= set(f) for f in data["findings"]))

    def test_a_commented_out_line_is_not_a_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "c.sh"
            f.write_text("#!/bin/bash\n# python3 \"$(dirname \"$common\")/scripts/docops.py\" --git-common-dir\n")
            code, out, _ = scan(f)
            self.assertEqual((code, ids_of(out)), (0, set()))

    def test_files_that_are_not_scripts_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "notes.md").write_text('python3 "$(dirname "$common")/scripts/docops.py" --git-common-dir\n')
            Path(tmp, "data.json").write_text('{"p": "~/.cache/tooltempest/repo/docs/x"}\n')
            code, out, _ = scan(tmp)
            self.assertEqual((code, ids_of(out)), (0, set()))

    def test_an_extensionless_file_with_a_shebang_is_scanned(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(FIXTURES / "p5_pos.sh", Path(tmp) / "pre-commit")
            code, out, _ = scan(tmp)
            self.assertEqual((code, ids_of(out)), (1, {"P5"}))


class ContractTests(unittest.TestCase):
    def test_a_missing_path_is_a_tool_error_not_a_pass(self):
        code, out, err = scan(FIXTURES / "does-not-exist.sh")
        self.assertEqual(code, 2)
        self.assertEqual(ids_of(out), set())  # no verdict is printed as a finding
        self.assertIn("does-not-exist.sh", err)

    def test_no_argument_is_a_usage_error(self):
        code, _, err = scan()
        self.assertEqual(code, 2)
        self.assertIn("usage", err.lower())

    def test_limits_lists_the_forms_the_scan_does_not_cover(self):
        code, out, _ = scan("--limits")
        self.assertEqual(code, 0)
        for needle in ("interpreter", "variable", "not covered"):
            self.assertIn(needle, out.lower())
        # the suppressions and exemptions that narrow the patterns are disclosed, with their real triggers
        for needle in ("`hashlib` or `sha256`", "`.digest(`", "exempt from P1 and P2", "first later line", "node_modules",
                       "core.fsmonitor", "a program on PATH", "`--allow` hides a finding", "launcher",
                       "interpreter held in a variable", "a `#` that follows whitespace inside a quoted element",
                       "the scan sees lines, not the program", "stops at the next `cd`", "the next two code lines",
                       "in no order", "UPPER_CASE token", "also inside a longer word",
                       "`python` followed only by digits and dots", "a variable after a flag is taken",
                       "Shell-shaped command text on any line", "a base held in a variable is not",
                       "or the user's Git configuration", "system configuration", "the inherited GIT_* environment"):
            self.assertIn(needle, " ".join(out.split()))  # the wrapping of the text does not matter

    def test_the_scan_changes_nothing_it_inspects(self):
        def digest():
            h = hashlib.sha256()
            for p in sorted(FIXTURES.iterdir()):
                h.update(p.name.encode() + p.read_bytes())
            return h.hexdigest()

        before = digest()
        scan(FIXTURES)
        self.assertEqual(before, digest())

    def test_the_script_uses_only_the_standard_library(self):
        tree = ast.parse(SCAN.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names.add(node.module.split(".")[0])
        self.assertLessEqual(names, set(sys.stdlib_module_names))

    def test_the_script_names_no_git_command_that_can_use_the_network(self):
        tree = ast.parse(SCAN.read_text(encoding="utf-8"))
        constants = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        self.assertFalse(constants & {"fetch", "pull", "push", "clone", "ls-remote", "remote", "submodule"})
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        self.assertFalse(imported & {"socket", "urllib", "http", "ssl"})


class AllowTests(unittest.TestCase):
    """`--allow PATTERN:PATHSUFFIX:REASON` is an explicit, reasoned exclusion. It hides nothing silently: the
    allowed finding is still printed with its reason, counted apart, and does not change the exit code."""

    ALLOW = "P4:p4_pos.py:installed together with its siblings in one directory"

    def test_an_allowed_finding_is_listed_with_its_reason_and_does_not_change_the_exit_code(self):
        code, out, err = scan("--allow", self.ALLOW, FIXTURES / "p4_pos.py")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(ids_of(out), set())
        self.assertIn("allowed P4", out)
        self.assertIn("installed together with its siblings in one directory", out)
        self.assertIn("0 finding(s), 1 allowed, 0 hygiene warning(s)", out)

    def test_the_exclusion_is_limited_to_its_pattern_and_its_file(self):
        # another pattern in the same file still reports
        code, out, _ = scan("--allow", "P1:incident_gate_like.py:accepted for this test", FIXTURES / "incident_gate_like.py")
        self.assertEqual((code, ids_of(out)), (1, {"P7"}))
        # the same pattern in another file still reports
        code, out, _ = scan("--allow", self.ALLOW, FIXTURES / "p4_pos.py", FIXTURES / "incident_pretool_like.py")
        self.assertEqual((code, ids_of(out)), (1, {"P4"}))
        self.assertTrue(any(line.split(":")[0].endswith("incident_pretool_like.py") for line in out.splitlines() if FINDING.match(line)))

    def test_a_path_suffix_separates_two_files_with_the_same_base_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            for sub in ("one", "two"):
                os.makedirs(Path(tmp, sub))
                shutil.copy(FIXTURES / "p4_pos.py", Path(tmp, sub, "guard.py"))
            code, out, _ = scan("--allow", "P4:one/guard.py:the first copy is reviewed", tmp)
            self.assertEqual(code, 1, out)
            lines = [line for line in out.splitlines() if FINDING.match(line)]
            self.assertTrue(lines and all("/two/guard.py" in line for line in lines), out)
            self.assertIn("/one/guard.py", "".join(line for line in out.splitlines() if "allowed P4" in line))
            # a bare base name is a suffix too: it covers both copies, and says so by listing both as allowed
            code, out, _ = scan("--allow", "P4:guard.py:both copies are reviewed", tmp)
            self.assertEqual(code, 0, out)
            self.assertIn("/one/guard.py", out)
            self.assertIn("/two/guard.py", out)

    def test_a_suffix_matches_whole_path_components_only(self):
        code, out, _ = scan("--allow", "P4:4_pos.py:partial name", FIXTURES / "p4_pos.py")
        self.assertEqual((code, ids_of(out)), (1, {"P4"}))
        self.assertIn("allow entry matched no finding: P4:4_pos.py", out)

    def test_a_missing_or_blank_reason_an_unknown_pattern_an_empty_suffix_or_a_repeated_entry_is_a_usage_error(self):
        for bad in (["P4:p4_pos.py"], ["P4:p4_pos.py:"], ["P4:p4_pos.py:   "], ["P9:p4_pos.py:why"], [":p4_pos.py:why"],
                    ["P4::why"], ["P4:/:why"], ["P4:p4_pos.py:why", "P4:p4_pos.py:another why"]):
            with self.subTest(allow=bad):
                args = []
                for a in bad:
                    args += ["--allow", a]
                code, out, err = scan(*args, FIXTURES / "p4_pos.py")
                self.assertEqual(code, 2, out + err)
                self.assertEqual(ids_of(out), set())

    def test_json_output_lists_allowed_findings_apart_from_findings(self):
        code, out, _ = scan("--format", "json", "--allow", self.ALLOW, "--allow", "P1:nowhere.py:unused", FIXTURES / "p4_pos.py")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["findings"], [])
        self.assertEqual({a["pattern"] for a in data["allowed"]}, {"P4"})
        self.assertTrue(all(a["reason"] and {"path", "line", "message"} <= set(a) for a in data["allowed"]))
        self.assertEqual(data["unused_allow"], ["P1:nowhere.py"])

    def test_overlapping_entries_are_all_counted_as_used(self):
        code, out, _ = scan("--allow", "P4:p4_pos.py:first reason", "--allow", "P4:fixtures/p4_pos.py:second reason",
                            FIXTURES / "p4_pos.py")
        self.assertEqual(code, 0, out)
        self.assertNotIn("matched no finding", out)

    def test_without_the_option_nothing_is_allowed(self):
        code, out, _ = scan(FIXTURES / "p4_pos.py")
        self.assertEqual((code, ids_of(out)), (1, {"P4"}))
        self.assertNotIn("allowed P4", out)


class LineNumberTests(unittest.TestCase):
    def test_a_form_feed_before_a_multi_line_call_does_not_shift_the_reported_line(self):
        src = ("import hashlib\nimport subprocess\n\x0c\n"
               "hashlib.sha256(checker)\n"
               "subprocess.run([sys.executable,\n    tool])\n")
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp, "m.py")
            f.write_text(src)
            code, out, _ = scan(f)
            self.assertEqual(code, 1, out)
            self.assertRegex(out, r"m\.py:6: P6: ")  # the `subprocess.run(` line, counted as splitlines counts


class HygieneTests(unittest.TestCase):
    """A clone that is behind, dirty or detached is reported. It never changes the exit code."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.upstream = self.tmp / "upstream"
        self.upstream.mkdir()
        git(self.upstream, "init", "-q", "-b", "main")
        (self.upstream / "a.txt").write_text("1\n")
        git(self.upstream, "add", "a.txt")
        git(self.upstream, "commit", "-q", "-m", "one")
        self.clone = self.tmp / "clone"
        git(self.tmp, "clone", "-q", str(self.upstream), str(self.clone))
        self.clean = self.tmp / "clean.sh"
        self.clean.write_text("#!/bin/bash\necho ok\n")

    def hygiene(self, *extra):
        return scan("--format", "json", "--clone", self.clone, *extra, self.clean)

    def kinds(self, out):
        return {h["kind"] for h in json.loads(out)["hygiene"]}

    def test_a_current_clean_clone_has_no_warning(self):
        code, out, _ = self.hygiene()
        self.assertEqual((code, self.kinds(out)), (0, set()))

    def test_a_dirty_clone_is_a_warning_and_exit_stays_zero(self):
        (self.clone / "a.txt").write_text("2\n")
        code, out, _ = self.hygiene()
        self.assertEqual((code, self.kinds(out)), (0, {"dirty"}))

    def test_an_untracked_file_makes_the_clone_dirty(self):
        (self.clone / "new.txt").write_text("x\n")
        code, out, _ = self.hygiene()
        self.assertEqual((code, self.kinds(out)), (0, {"dirty"}))

    def test_a_detached_clone_is_a_warning(self):
        git(self.clone, "checkout", "-q", "--detach")
        code, out, _ = self.hygiene()
        self.assertEqual((code, self.kinds(out)), (0, {"detached"}))

    def test_a_clone_behind_its_cached_upstream_ref_is_a_warning_and_no_network_command_is_run(self):
        (self.upstream / "a.txt").write_text("3\n")
        git(self.upstream, "commit", "-q", "-am", "two")
        git(self.clone, "fetch", "-q")  # the test fetches; the scan must not
        log = self.tmp / "git-calls.log"
        wrapper_dir = self.tmp / "bin"
        wrapper_dir.mkdir()
        real_git = shutil.which("git")
        wrapper = wrapper_dir / "git"
        wrapper.write_text('#!/bin/sh\necho "$@" >> "%s"\nexec "%s" "$@"\n' % (log, real_git))
        wrapper.chmod(0o755)
        env = dict(os.environ, PATH=str(wrapper_dir) + os.pathsep + os.environ["PATH"])
        code, out, _ = scan("--format", "json", "--clone", self.clone, self.clean, env=env)
        self.assertEqual((code, self.kinds(out)), (0, {"behind"}))
        calls = log.read_text()
        self.assertIn("rev-list", calls)  # the wrapper saw the scan's Git calls
        subcommands = set()
        for line in calls.splitlines():
            words, i = line.split(" "), 0
            while i < len(words) and words[i] in ("-C", "-c"):
                i += 2
            subcommands.add(words[i])
        self.assertFalse(subcommands & {"fetch", "pull", "ls-remote", "push", "clone", "remote", "submodule"}, subcommands)

    def test_the_fsmonitor_program_of_the_clone_is_not_started(self):
        marker = self.tmp / "fsmonitor-ran"
        hook = self.tmp / "fsmon.sh"
        hook.write_text('#!/bin/sh\ntouch "%s"\nprintf ""\n' % marker)
        hook.chmod(0o755)
        git(self.clone, "config", "core.fsmonitor", str(hook))
        (self.clone / "a.txt").write_text("2\n")
        code, out, _ = self.hygiene()
        self.assertEqual((code, self.kinds(out)), (0, {"dirty"}))
        self.assertFalse(marker.exists())

    def test_a_subdirectory_of_a_clone_is_not_accepted_as_the_clone(self):
        sub = self.clone / "inner"
        sub.mkdir()
        code, _, err = scan("--clone", sub, self.clean)
        self.assertEqual(code, 2)
        self.assertIn("inner", err)

    def test_hygiene_and_findings_are_independent(self):
        (self.clone / "a.txt").write_text("2\n")
        code, out, _ = scan("--format", "json", "--clone", self.clone, FIXTURES / "p5_pos.sh")
        self.assertEqual(code, 1)
        data = json.loads(out)
        self.assertEqual(({f["pattern"] for f in data["findings"]}, {h["kind"] for h in data["hygiene"]}),
                         ({"P5"}, {"dirty"}))

    def test_an_inherited_git_environment_does_not_redirect_the_hygiene_check(self):
        other = self.tmp / "other"
        other.mkdir()
        git(other, "init", "-q")
        env = dict(os.environ, GIT_DIR=str(other / ".git"), GIT_WORK_TREE=str(other))
        (self.clone / "a.txt").write_text("2\n")
        code, out, _ = scan("--format", "json", "--clone", self.clone, self.clean, env=env)
        self.assertEqual((code, self.kinds(out)), (0, {"dirty"}))

    def test_a_clone_path_that_is_not_a_repository_is_reported_and_not_a_pass(self):
        code, out, err = scan("--clone", self.tmp / "nowhere", self.clean)
        self.assertEqual(code, 2)
        self.assertIn("nowhere", err)


if __name__ == "__main__":
    unittest.main()
