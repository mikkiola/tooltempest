"""scripts/docops.py end to end (isolated-mode subprocesses against scratch
repositories): exit codes, result schema, push semantics, failure modes,
and the no-mutation guarantee."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from helpers import (DOCOPS_PY, SCRIPTS_DIR, DocopsTestCase, GOOD_SPEC, GitFixture, IDLE_SPEC,
                     clean_env, run_check_json, run_cli, tree_digest)

BAD_SPEC = "# Broken\n\n## Goals\n\n- [ ] only a goal\n"
ZERO = "0" * 40


def push_line(local_ref, local_oid, remote_ref="refs/heads/main", remote_oid=ZERO):
    return "%s %s %s %s\n" % (local_ref, local_oid, remote_ref, remote_oid)


class CheckCommandTests(DocopsTestCase):
    def setUp(self):
        self.fx = GitFixture()
        self.addCleanup(self.fx.cleanup)

    def run_check(self, *args, **kwargs):
        return run_check_json(["--repo", self.fx.root, *args], **kwargs)

    def test_exit_codes_and_schema_for_each_status(self):
        fx = self.fx
        cases = {}
        fx.commit_files({"README.md": "x\n"})
        cases["N/A"] = fx.commit_files({"SPEC.md": IDLE_SPEC}, "idle")
        cases["PASS"] = fx.commit_files({"SPEC.md": GOOD_SPEC}, "good")
        cases["FAIL"] = fx.commit_files({"SPEC.md": BAD_SPEC}, "bad")
        fx.git("rm", "-q", "SPEC.md")
        fx.stage("deep/er/SPEC.md", GOOD_SPEC)
        cases["SKIP"] = fx.commit("nested only")
        expected_exit = {"N/A": 0, "PASS": 0, "FAIL": 1, "SKIP": 3}
        for status, oid in cases.items():
            code, result, err = self.run_check("--tree", oid)
            self.assertEqual(result["snapshots"][0]["status"], status)
            self.assertEqual((code, result["summary"]["exit_code"]), (expected_exit[status],) * 2)
            self.assertValidResult(result)
            self.assertEqual(err, "")
        code, result, _ = self.run_check("--tree", "no-such-ref")
        self.assertEqual((code, result["summary"]["status"]), (2, None))
        self.assertToolError(result["snapshots"][0], "SNAPSHOT_UNREADABLE")
        self.assertValidResult(result)

    def test_result_shape_and_snapshot_identity(self):
        oid = self.fx.commit_files({"SPEC.md": GOOD_SPEC})
        code, result, _ = self.run_check("--tree", "HEAD", "--tree", "index")
        self.assertEqual(code, 0)
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["tool"]["name"], "docops")
        self.assertEqual(result["command"], "check")
        self.assertEqual(result["repository"]["root"], self.fx.root)
        head, index = result["snapshots"]
        self.assertEqual(head["snapshot"]["commit"], oid)
        self.assertEqual(head["snapshot"]["tree"], self.fx.git("rev-parse", "HEAD^{tree}"))
        self.assertEqual(index["snapshot"]["kind"], "index")
        self.assertEqual(list(head["checks"]),
                         ["SPEC_STRUCTURE", "TESTS", "DOC_IMPACT_ASSESSMENT",
                          "DOC_CONSISTENCY_REVIEW", "ACCEPTANCE_VERIFICATION", "PUBLICATION",
                          "OWNER_ACCEPTANCE"])
        track = head["checks"]["SPEC_STRUCTURE"]["tracks"][0]
        self.assertEqual((track["track"], track["state"], track["milestones"]),
                         ("root", "active", {"total": 2, "checked": 1, "unchecked": 1}))

    def test_one_failing_snapshot_makes_the_whole_run_fail(self):
        good = self.fx.commit_files({"SPEC.md": GOOD_SPEC}, "good")
        bad = self.fx.commit_files({"SPEC.md": BAD_SPEC}, "bad")
        code, result, _ = self.run_check("--tree", good, "--tree", bad, "--tree", good)
        self.assertEqual(code, 1)
        self.assertEqual([s["status"] for s in result["snapshots"]], ["PASS", "FAIL", "PASS"])

    def test_run_from_subdirectory_uses_repository_root(self):
        self.fx.commit_files({"SPEC.md": GOOD_SPEC, "sub/dir/x.txt": "x\n"})
        cwd = os.path.join(self.fx.root, "sub", "dir")
        code, result, _ = run_check_json(["--tree", "index"], cwd=cwd)
        self.assertEqual((code, result["repository"]["root"]), (0, self.fx.root))

    def test_index_file_override_is_honoured_like_in_a_hook(self):
        self.fx.commit_files({"SPEC.md": GOOD_SPEC})
        alt = os.path.join(os.path.dirname(self.fx.root), "alt-index")
        env = clean_env({"GIT_INDEX_FILE": alt})
        import subprocess
        subprocess.run(["git", "-C", self.fx.root, "read-tree", "HEAD"], env=env, check=True)
        blob = subprocess.run(["git", "-C", self.fx.root, "hash-object", "-w", "--stdin"],
                              input=BAD_SPEC.encode(), capture_output=True, env=env,
                              check=True).stdout.decode().strip()
        subprocess.run(["git", "-C", self.fx.root, "update-index", "--cacheinfo",
                        "100644,%s,SPEC.md" % blob], env=env, check=True)
        code, result, _ = self.run_check("--tree", "index", env=env)
        self.assertEqual(code, 1)  # the intended (alternate) index, not .git/index
        code, result, _ = self.run_check("--tree", "index")
        self.assertEqual(code, 0)

    def test_linked_worktree_has_its_own_index(self):
        self.fx.commit_files({"SPEC.md": GOOD_SPEC})
        wt = os.path.join(os.path.dirname(self.fx.root), "linked")
        self.fx.git("worktree", "add", "-q", "-b", "wt", wt)
        with open(os.path.join(wt, "SPEC.md"), "w") as handle:
            handle.write(BAD_SPEC)
        import subprocess
        subprocess.run(["git", "-C", wt, "add", "SPEC.md"], env=clean_env(), check=True)
        code, _, _ = run_check_json(["--repo", wt, "--tree", "index"])
        self.assertEqual(code, 1)
        code, _, _ = self.run_check("--tree", "index")
        self.assertEqual(code, 0)

    def test_bare_repository_checks_commits_but_has_no_index(self):
        self.fx.commit_files({"SPEC.md": GOOD_SPEC})
        bare = os.path.join(os.path.dirname(self.fx.root), "bare.git")
        import subprocess
        subprocess.run(["git", "clone", "-q", "--bare", self.fx.root, bare], env=clean_env(),
                       check=True)
        code, result, _ = run_check_json(["--repo", bare, "--tree", "main"])
        self.assertEqual((code, result["snapshots"][0]["status"]), (0, "PASS"))
        code, result, _ = run_check_json(["--repo", bare, "--tree", "index"])
        self.assertEqual(code, 2)
        self.assertToolError(result["snapshots"][0])


class PushStdinTests(DocopsTestCase):
    def setUp(self):
        self.fx = GitFixture()
        self.addCleanup(self.fx.cleanup)
        self.fx.commit_files({"SPEC.md": GOOD_SPEC}, "main good")
        self.main_oid = self.fx.git("rev-parse", "HEAD")
        self.fx.git("switch", "-q", "-c", "feature")
        self.feature_oid = self.fx.commit_files({"SPEC.md": BAD_SPEC}, "feature bad")
        self.fx.git("switch", "-q", "main")  # checkout and disk are good

    def push(self, *lines, extra=()):
        stdin = "".join(lines).encode()
        return run_check_json(["--repo", self.fx.root, "--push-stdin", *extra], stdin=stdin)

    def test_pushed_non_head_ref_is_judged_by_its_oid(self):
        code, result, _ = self.push(push_line("refs/heads/feature", self.feature_oid))
        self.assertEqual(code, 1)
        snap = result["snapshots"][0]
        self.assertEqual(snap["snapshot"]["commit"], self.feature_oid)
        self.assertEqual(snap["request"]["local_ref"], "refs/heads/feature")
        self.assertEqual(snap["request"]["remote_ref"], "refs/heads/main")

    def test_good_pushed_ref_passes_even_when_checkout_is_bad(self):
        self.fx.git("switch", "-q", "feature")  # checkout is now the bad branch
        code, result, _ = self.push(push_line("refs/heads/main", self.main_oid))
        self.assertEqual((code, result["snapshots"][0]["status"]), (0, "PASS"))

    def test_multiple_refs_including_a_deleted_one(self):
        code, result, _ = self.push(
            push_line("refs/heads/main", self.main_oid, remote_oid=self.main_oid),
            push_line("refs/heads/feature", self.feature_oid, "refs/heads/feature"),
            push_line("(delete)", ZERO, "refs/heads/old", self.main_oid),
        )
        self.assertEqual(code, 1)
        self.assertValidResult(result)
        statuses = [s["status"] for s in result["snapshots"]]
        self.assertEqual(statuses, ["PASS", "FAIL", "N/A"])
        deleted = result["snapshots"][2]
        self.assertEqual(deleted["snapshot"]["kind"], "deleted-ref")
        self.assertEqual(deleted["request"]["kind"], "deleted-ref")
        self.assertIn("deletion", deleted["checks"]["SPEC_STRUCTURE"]["reason"])

    def test_deletion_is_not_masked_by_or_masking_a_failure(self):
        code, result, _ = self.push(
            push_line("(delete)", ZERO, "refs/heads/old", self.main_oid),
            push_line("refs/heads/feature", self.feature_oid),
        )
        self.assertEqual(code, 1)

    def test_only_deletions_is_na_exit_zero(self):
        code, result, _ = self.push(push_line("(delete)", ZERO, "refs/heads/old", self.main_oid))
        self.assertEqual((code, result["summary"]["status"]), (0, "N/A"))

    def test_malformed_line_is_error_but_other_refs_are_still_judged(self):
        code, result, _ = self.push("garbage line\n",
                                    push_line("refs/heads/feature", self.feature_oid))
        self.assertEqual(code, 2)
        self.assertEqual([s["status"] for s in result["snapshots"]], [None, "FAIL"])
        self.assertEqual(result["summary"]["status"], None)  # partial results are no verdict
        self.assertEqual(result["snapshots"][0]["request"]["kind"], "invalid-update")
        self.assertValidResult(result)

    def test_ref_names_with_unicode_line_separators_are_not_split(self):
        code, result, _ = self.push(push_line("refs/heads/a\u2028b\u0085c", self.main_oid))
        self.assertEqual((code, len(result["snapshots"])), (0, 1))
        self.assertEqual(result["snapshots"][0]["request"]["local_ref"], "refs/heads/a\u2028b\u0085c")

    def test_crlf_push_lines_are_accepted(self):
        code, _, _ = self.push(push_line("refs/heads/main", self.main_oid).replace("\n", "\r\n"))
        self.assertEqual(code, 0)

    def test_empty_stdin_is_error_not_pass(self):
        code, result, _ = self.push()
        self.assertEqual((code, result["summary"]["status"]), (2, None))
        self.assertIn("no ref updates", result["summary"]["reason"])

    def test_blank_lines_only_is_error(self):
        code, _, _ = self.push("\n\n")
        self.assertEqual(code, 2)

    def test_non_oid_local_field_is_error_not_resolved_as_a_ref(self):
        for local in ("main", "HEAD", self.main_oid[:12], self.main_oid.upper()):
            code, result, _ = self.push(push_line("refs/heads/main", local))
            self.assertEqual(code, 2, local)
            self.assertEqual(result["snapshots"][0]["request"]["kind"], "invalid-update", local)

    def test_unknown_oid_is_error(self):
        code, result, _ = self.push(push_line("refs/heads/x", "1" * 40))
        self.assertEqual(code, 2)
        self.assertToolError(result["snapshots"][0])

    def test_non_commit_object_is_error(self):
        tree = self.fx.git("rev-parse", "HEAD^{tree}")
        code, _, _ = self.push(push_line("refs/heads/x", tree))
        self.assertEqual(code, 2)

    def test_annotated_tag_object_is_peeled(self):
        self.fx.git("tag", "-a", "-m", "t", "v1", self.feature_oid)
        tag_object = self.fx.git("rev-parse", "v1")
        self.assertNotEqual(tag_object, self.feature_oid)
        code, result, _ = self.push(push_line("refs/tags/v1", tag_object, "refs/tags/v1"))
        self.assertEqual(code, 1)
        self.assertEqual(result["snapshots"][0]["snapshot"]["commit"], self.feature_oid)

    def test_trees_and_push_stdin_combine(self):
        code, result, _ = self.push(push_line("refs/heads/main", self.main_oid),
                                    extra=("--tree", "index"))
        self.assertEqual(code, 0)
        self.assertEqual([s["request"]["kind"] for s in result["snapshots"]], ["index", "rev"])

    def test_trees_with_empty_stdin_still_run(self):
        code, result, _ = self.push(extra=("--tree", "HEAD"))
        self.assertEqual((code, len(result["snapshots"])), (0, 1))


class FailureModeTests(DocopsTestCase):
    def assertErrorRun(self, code, out):
        self.assertEqual(code, 2)
        self.assertNotIn('"PASS"', out)
        self.assertNotIn('"N/A"', out)
        self.assertNotIn('"ERROR"', out)  # not a status value

    def test_usage_errors_exit_two(self):
        for args in ([], ["check"], ["report"], ["bogus"], ["check", "--bogus"],
                     ["check", "--tree"], ["check", "--repo", "."]):
            code, out, err = run_cli(args)
            self.assertEqual(code, 2, args)
            self.assertNotIn("PASS", out, args)

    def test_nonexistent_repo_path(self):
        code, out, _ = run_cli(["check", "--repo", "/definitely/not/here", "--tree", "index"])
        self.assertErrorRun(code, out)
        result = json.loads(out)
        self.assertEqual((result["summary"]["status"], result["summary"]["exit_code"]), (None, 2))
        self.assertIn("could not be opened", result["summary"]["reason"])
        self.assertValidResult(result)

    def test_not_a_git_repository(self):
        with tempfile.TemporaryDirectory() as plain:
            code, out, _ = run_cli(["check", "--repo", plain, "--tree", "index"])
        self.assertErrorRun(code, out)
        self.assertValidResult(json.loads(out))

    def test_git_not_installed(self):
        with GitFixture() as fx, tempfile.TemporaryDirectory() as empty:
            fx.commit_files({"SPEC.md": GOOD_SPEC})
            env = clean_env({"PATH": empty})
            code, out, _ = run_cli(["check", "--repo", fx.root, "--tree", "HEAD"], env=env)
        self.assertErrorRun(code, out)
        self.assertIn("not found", json.loads(out)["summary"]["reason"])

    def test_missing_package_dependency(self):
        with tempfile.TemporaryDirectory() as lone:
            script = os.path.join(lone, "docops.py")
            shutil.copy(str(DOCOPS_PY), script)  # without docops_core
            with GitFixture() as fx:
                fx.commit_files({"SPEC.md": GOOD_SPEC})
                code, out, _ = run_cli(["check", "--repo", fx.root, "--tree", "HEAD"],
                                       script=script)
        self.assertErrorRun(code, out)
        result = json.loads(out)
        self.assertEqual((result["summary"]["status"], result["summary"]["exit_code"]), (None, 2))
        self.assertIn("could not be imported", result["summary"]["reason"])
        self.assertValidResult(result)

    def test_broken_package_module(self):
        with tempfile.TemporaryDirectory() as copy:
            shutil.copytree(str(SCRIPTS_DIR / "docops_core"), os.path.join(copy, "docops_core"),
                            ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy(str(DOCOPS_PY), os.path.join(copy, "docops.py"))
            with open(os.path.join(copy, "docops_core", "specparse.py"), "w") as handle:
                handle.write("def broken(:\n")
            with GitFixture() as fx:
                fx.commit_files({"SPEC.md": GOOD_SPEC})
                code, out, _ = run_cli(["check", "--repo", fx.root, "--tree", "HEAD"],
                                       script=os.path.join(copy, "docops.py"))
        self.assertErrorRun(code, out)

    def test_internal_error_in_command_is_error_result(self):
        from docops_core import cli
        buffer = io.StringIO()
        with mock.patch.object(cli, "_evaluate", side_effect=RuntimeError("boom")), \
                contextlib.redirect_stdout(buffer):
            code = cli.main(["check", "--tree", "index"])
        self.assertEqual(code, 2)
        result = json.loads(buffer.getvalue())
        self.assertEqual((result["summary"]["status"], result["summary"]["exit_code"]), (None, 2))
        self.assertIn("internal error: RuntimeError", result["summary"]["reason"])
        self.assertValidResult(result)

    def test_rendering_fault_exits_two_not_the_fail_code(self):
        from docops_core import cli
        with GitFixture() as fx:
            fx.commit_files({"SPEC.md": GOOD_SPEC})
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(cli, "render_text", side_effect=RuntimeError("boom")), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["report", "--repo", fx.root, "--tree", "HEAD"])
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("internal error while rendering", err.getvalue())

    def test_broken_stdout_is_a_failure_exit(self):
        from docops_core import cli

        class Broken(io.StringIO):
            def write(self, _):
                raise BrokenPipeError()

        with GitFixture() as fx:
            fx.commit_files({"SPEC.md": GOOD_SPEC})
            with mock.patch("sys.stdout", Broken()):
                self.assertEqual(cli.main(["check", "--repo", fx.root, "--tree", "HEAD"]), 2)


class ReportCommandTests(DocopsTestCase):
    def test_report_text_lists_every_dimension_and_matches_check_exit(self):
        with GitFixture() as fx:
            fx.commit_files({"SPEC.md": BAD_SPEC})
            code, out, _ = run_cli(["report", "--repo", fx.root, "--tree", "HEAD"])
            check_code, _, _ = run_check_json(["--repo", fx.root, "--tree", "HEAD"])
        self.assertEqual((code, check_code), (1, 1))
        for name in ("SPEC_STRUCTURE", "TESTS", "DOC_IMPACT_ASSESSMENT", "DOC_CONSISTENCY_REVIEW",
                     "ACCEPTANCE_VERIFICATION", "PUBLICATION", "OWNER_ACCEPTANCE"):
            self.assertIn(name, out)
        self.assertIn("not assessed", out)
        self.assertNotIn("NOT_EVALUATED", out)
        self.assertIn("MILESTONES_MISSING", out)
        self.assertIn("summary: FAIL (exit 1)", out)

    def test_report_for_na_does_not_say_pass(self):
        with GitFixture() as fx:
            fx.commit_files({"README.md": "x\n"})
            code, out, _ = run_cli(["report", "--repo", fx.root, "--tree", "index"])
        self.assertEqual(code, 0)
        self.assertIn("summary: N/A (exit 0)", out)
        self.assertNotIn("PASS", out)

    def test_report_escapes_control_characters_from_snapshot_paths(self):
        with GitFixture() as fx:
            oid = fx.commit_files({"SPEC.md": GOOD_SPEC, "a\x1b[31mred/SPEC.md": BAD_SPEC})
            code, out, _ = run_cli(["report", "--repo", fx.root, "--tree", oid])
        self.assertEqual(code, 0)
        self.assertNotIn("\x1b", out)
        self.assertIn("a\\x1b[31mred/SPEC.md", out)

    def test_report_survives_non_utf8_paths_and_bidi_controls(self):
        import subprocess
        with GitFixture() as fx:
            fx.commit_files({"SPEC.md": GOOD_SPEC})
            blob = fx.git("hash-object", "-w", "--stdin", input_bytes=BAD_SPEC.encode())
            path = b"bad\xffname/SPEC.md"
            subprocess.run(["git", "-C", fx.root, "update-index", "--add", "--cacheinfo",
                            b"100644," + blob.encode() + b"," + path],
                           env=clean_env(), check=True)
            fx.git("commit", "-q", "-m", "odd path")
            fx.stage("bidi\u202e/SPEC.md", BAD_SPEC)
            fx.commit("bidi")
            code, out, err = run_cli(["report", "--repo", fx.root, "--tree", "HEAD"])
            check_code, result, _ = run_check_json(["--repo", fx.root, "--tree", "HEAD"])
        self.assertEqual((code, check_code), (0, 0))
        self.assertEqual(err, "")
        self.assertIn("OUT_OF_SCOPE_SPEC", out)
        self.assertNotIn("\u202e", out)
        self.assertIn("\\u202e", out)
        self.assertIn("\\udcff", out)
        self.assertValidResult(result)

    def test_report_error_shows_reason(self):
        code, out, _ = run_cli(["report", "--repo", "/definitely/not/here", "--tree", "index"])
        self.assertEqual(code, 2)
        self.assertIn("summary: NO VERDICT (tool error) (exit 2)", out)

    def test_report_with_push_stdin_and_out_of_scope_warning(self):
        with GitFixture() as fx:
            oid = fx.commit_files({"SPEC.md": GOOD_SPEC, "a/b/SPEC.md": BAD_SPEC})
            code, out, _ = run_cli(["report", "--repo", fx.root, "--push-stdin"],
                                   stdin=push_line("refs/heads/main", oid).encode())
        self.assertEqual(code, 0)
        self.assertIn("OUT_OF_SCOPE_SPEC", out)
        self.assertIn("refs/heads/main", out)


class NoMutationTests(DocopsTestCase):
    """Running the checker changes no file, index, config, ref or object."""

    def build(self, fx):
        fx.commit_files({"SPEC.md": GOOD_SPEC, "a/b/SPEC.md": BAD_SPEC, "code.py": "x = 1\n"}, "one")
        fx.git("switch", "-q", "-c", "feature")
        feature = fx.commit_files({"SPEC.md": BAD_SPEC}, "two")
        fx.git("switch", "-q", "main")
        fx.stage("SPEC.md", GOOD_SPEC.replace("first", "second"))
        fx.write("code.py", "x = 2\n")  # unstaged change
        fx.write("untracked.txt", "u\n")
        os.utime(os.path.join(fx.root, "SPEC.md"), None)  # index entry is now stat-dirty
        return feature

    def test_repository_is_byte_identical_after_every_command(self):
        with GitFixture() as fx:
            feature = self.build(fx)
            before = tree_digest(fx.root)
            runs = [
                ["check", "--tree", "index"],
                ["check", "--tree", "HEAD", "--tree", "feature", "--tree", "no-such"],
                ["report", "--tree", "index", "--tree", "feature"],
                ["check", "--tree", "--bogus"],
            ]
            for args in runs:
                run_cli(args[:1] + ["--repo", fx.root] + args[1:])
            run_cli(["check", "--repo", fx.root, "--push-stdin"],
                    stdin=(push_line("refs/heads/feature", feature)
                           + push_line("(delete)", ZERO, "refs/heads/x", feature)).encode())
            run_cli(["check", "--repo", fx.root, "--tree", "index"],
                    env=clean_env({"PATH": tempfile.gettempdir()}))  # git missing path
            self.assertEqual(tree_digest(fx.root), before)

    def test_checker_writes_no_bytecode_or_other_files_next_to_itself(self):
        with tempfile.TemporaryDirectory() as copy, GitFixture() as fx:
            shutil.copytree(str(SCRIPTS_DIR / "docops_core"), os.path.join(copy, "docops_core"),
                            ignore=shutil.ignore_patterns("__pycache__"))
            script = os.path.join(copy, "docops.py")
            shutil.copy(str(DOCOPS_PY), script)
            fx.commit_files({"SPEC.md": GOOD_SPEC})
            before = tree_digest(copy)
            # PYTHONDONTWRITEBYTECODE deliberately unset: the script must opt out itself.
            env = {k: v for k, v in clean_env().items() if k != "PYTHONDONTWRITEBYTECODE"}
            code, _, _ = run_cli(["check", "--repo", fx.root, "--tree", "HEAD"],
                                 script=script, env=env)
            self.assertEqual(code, 0)
            self.assertEqual(tree_digest(copy), before)
            self.assertFalse(any(name == "__pycache__" for _, dirs, _ in os.walk(copy)
                                 for name in dirs))

    def test_in_process_library_use_leaves_repository_untouched(self):
        from docops_core import checks, gitstate
        with GitFixture() as fx:
            self.build(fx)
            before = tree_digest(fx.root)
            repo = gitstate.open_repository(fx.root)
            for request in ({"kind": "index"}, {"kind": "rev", "rev": "HEAD"},
                            {"kind": "rev", "rev": "nope"}):
                checks.check_request(repo, request)
            self.assertEqual(tree_digest(fx.root), before)


if __name__ == "__main__":
    unittest.main()
