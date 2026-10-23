"""Git-state semantics: the checker inspects exactly the snapshot it is
pointed at, and no failure of the checker or the input reads as PASS/N/A."""
import json
import os
import shutil
import stat
import tempfile
import unittest
from unittest import mock

from helpers import (DocopsTestCase, GOOD_SPEC, GitFixture, IDLE_SPEC, load_result_schema,
                     validate)

from docops_core import checks, gitstate, specparse
from docops_core.gitstate import GitError

BAD_SPEC = "# Broken\n\n## Goals\n\n- [ ] only a goal, no Milestones section\n"
SPEC_V2 = GOOD_SPEC.replace("M1: first thing", "M1: edited thing")


def check(fixture, tree):
    repo = gitstate.open_repository(fixture.root)
    request = {"kind": "index"} if tree == "index" else {"kind": "rev", "rev": tree}
    result = checks.check_request(repo, request)
    return result


def spec_status(result):
    """SPEC_STRUCTURE status, or 'TOOL_ERROR' when the checker produced no verdict."""
    if result["error"] is not None:
        return "TOOL_ERROR"
    return result["checks"]["SPEC_STRUCTURE"]["status"]


def tracks(result):
    return {t["track"]: t for t in result["checks"]["SPEC_STRUCTURE"]["tracks"]}


def policy_json(*specs):
    return json.dumps({"schema_version": 1,
                       "specs": [{"path": p, "track": t} for p, t in specs]})


class SnapshotSelectionTests(DocopsTestCase):
    def test_index_not_worktree_unstaged_damage_is_invisible(self):
        with GitFixture() as fx:
            fx.commit_files({"SPEC.md": GOOD_SPEC})
            fx.write("SPEC.md", BAD_SPEC)  # unstaged edit on disk
            result = check(fx, "index")
            self.assertEqual(spec_status(result), "PASS")
            self.assertValidResult({"schema_version": 1, "tool": {"name": "docops", "version": "x"},
                                    "command": "check", "repository": {"root": fx.root},
                                    "snapshots": [result],
                                    "summary": {"status": "PASS", "exit_code": 0,
                                                "tool_errors": 0}})

    def test_bad_staged_content_fails_even_if_disk_is_good(self):
        with GitFixture() as fx:
            fx.commit_files({"SPEC.md": GOOD_SPEC})
            fx.stage("SPEC.md", BAD_SPEC)
            fx.write("SPEC.md", GOOD_SPEC)  # disk looks fine again
            result = check(fx, "index")
            self.assertEqual(spec_status(result), "FAIL")
            self.assertEqual(result["exit_code"], 1)

    def test_good_staged_passes_when_disk_deleted(self):
        with GitFixture() as fx:
            fx.stage("SPEC.md", GOOD_SPEC)
            os.remove(os.path.join(fx.root, "SPEC.md"))
            self.assertEqual(spec_status(check(fx, "index")), "PASS")

    def test_untracked_disk_spec_does_not_exist_for_the_index(self):
        with GitFixture() as fx:
            fx.commit_files({"README.md": "x\n"})
            fx.write("SPEC.md", BAD_SPEC)  # untracked, never staged
            result = check(fx, "index")
            self.assertEqual(spec_status(result), "N/A")
            self.assertEqual(result["snapshot"]["kind"], "index")

    def test_index_vs_arbitrary_commit_vs_head(self):
        with GitFixture() as fx:
            first = fx.commit_files({"SPEC.md": GOOD_SPEC}, "good")
            head = fx.commit_files({"SPEC.md": BAD_SPEC}, "bad")
            fx.stage("SPEC.md", SPEC_V2)  # index differs from HEAD again
            by_commit = check(fx, first)
            by_head = check(fx, "HEAD")
            by_index = check(fx, "index")
            self.assertEqual(
                (spec_status(by_commit), spec_status(by_head), spec_status(by_index)),
                ("PASS", "FAIL", "PASS"))
            self.assertEqual(by_commit["snapshot"]["commit"], first)
            self.assertEqual(by_head["snapshot"]["commit"], head)
            self.assertIsNone(by_commit["snapshot"]["index_digest"])
            self.assertIsNotNone(by_index["snapshot"]["index_digest"])
            self.assertNotEqual(by_commit["snapshot"]["tree"], by_head["snapshot"]["tree"])

    def test_index_digest_tracks_index_content(self):
        with GitFixture() as fx:
            fx.stage("SPEC.md", GOOD_SPEC)
            before = check(fx, "index")["snapshot"]["index_digest"]
            again = check(fx, "index")["snapshot"]["index_digest"]
            fx.stage("SPEC.md", SPEC_V2)
            after = check(fx, "index")["snapshot"]["index_digest"]
            self.assertEqual(before, again)
            self.assertNotEqual(before, after)

    def test_non_head_ref_is_checked_by_its_own_oid(self):
        with GitFixture() as fx:
            fx.commit_files({"SPEC.md": GOOD_SPEC}, "main good")
            fx.git("switch", "-q", "-c", "feature")
            feature_oid = fx.commit_files({"SPEC.md": BAD_SPEC}, "feature bad")
            fx.git("switch", "-q", "main")  # checkout and disk are good
            with open(os.path.join(fx.root, "SPEC.md"), encoding="utf-8") as handle:
                self.assertEqual(handle.read(), GOOD_SPEC)
            for rev in ("feature", "refs/heads/feature", feature_oid, feature_oid[:12]):
                result = check(fx, rev)
                self.assertEqual(spec_status(result), "FAIL", rev)
                self.assertEqual(result["snapshot"]["commit"], feature_oid)
            self.assertEqual(spec_status(check(fx, "HEAD")), "PASS")

    def test_bad_checkout_does_not_taint_a_good_non_head_ref(self):
        with GitFixture() as fx:
            good = fx.commit_files({"SPEC.md": GOOD_SPEC}, "good")
            fx.git("branch", "good-branch")
            fx.commit_files({"SPEC.md": BAD_SPEC}, "bad head")
            result = check(fx, "good-branch")
            self.assertEqual(spec_status(result), "PASS")
            self.assertEqual(result["snapshot"]["commit"], good)

    def test_annotated_tag_is_peeled_to_its_commit(self):
        with GitFixture() as fx:
            oid = fx.commit_files({"SPEC.md": GOOD_SPEC})
            fx.git("tag", "-a", "-m", "t", "v1")
            result = check(fx, "v1")
            self.assertEqual((spec_status(result), result["snapshot"]["commit"]), ("PASS", oid))

    def test_missing_spec_is_na_in_index_and_commit(self):
        with GitFixture() as fx:
            fx.commit_files({"README.md": "x\n"})
            for tree in ("index", "HEAD"):
                result = check(fx, tree)
                self.assertEqual(spec_status(result), "N/A", tree)
                self.assertEqual(result["exit_code"], 0)
                self.assertEqual(result["checks"]["SPEC_STRUCTURE"]["tracks"], [])

    def test_repository_without_commits(self):
        with GitFixture() as fx:
            self.assertEqual(spec_status(check(fx, "index")), "N/A")
            result = check(fx, "HEAD")  # unborn branch: cannot be read, must not be N/A
            self.assertToolError(result, "SNAPSHOT_UNREADABLE")

    def test_unresolvable_inputs_are_errors(self):
        with GitFixture() as fx:
            fx.commit_files({"SPEC.md": GOOD_SPEC})
            for rev in ("no-such-ref", "HEAD^{tree}", "--all", "-h", "", "HEAD~50", "a\nb",
                        "0" * 40):
                result = check(fx, rev)
                self.assertToolError(result, "SNAPSHOT_UNREADABLE")
                self.assertEqual(result["snapshot"]["kind"], "unresolved")

    def test_unmerged_spec_in_index_fails(self):
        with GitFixture() as fx:
            fx.commit_files({"SPEC.md": GOOD_SPEC}, "base")
            fx.git("switch", "-q", "-c", "other")
            fx.commit_files({"SPEC.md": SPEC_V2}, "other")
            fx.git("switch", "-q", "main")
            fx.commit_files({"SPEC.md": GOOD_SPEC.replace("second thing", "main thing")}, "main")
            fx.git("merge", "other", check=False)
            self.assertIn("UU", fx.git("status", "--porcelain"))
            result = check(fx, "index")
            self.assertEqual(spec_status(result), "FAIL")
            self.assertEqual(tracks(result)["root"]["diagnostics"][0]["code"], "SPEC_UNMERGED")

    def test_unmerged_policy_in_index_is_error(self):
        with GitFixture() as fx:
            base = policy_json(("SPEC.md", "a"))
            fx.commit_files({"SPEC.md": GOOD_SPEC, ".docops-policy.json": base}, "base")
            fx.git("switch", "-q", "-c", "other")
            fx.commit_files({".docops-policy.json": policy_json(("SPEC.md", "b"))}, "other")
            fx.git("switch", "-q", "main")
            fx.commit_files({".docops-policy.json": policy_json(("SPEC.md", "c"))}, "main")
            fx.git("merge", "other", check=False)
            self.assertToolError(check(fx, "index"))

    def test_directory_at_the_spec_path_is_not_absence(self):
        with GitFixture() as fx:
            fx.commit_files({"SPEC.md/notes.txt": "x\n"})
            result = check(fx, "HEAD")
            self.assertEqual(spec_status(result), "FAIL")
            self.assertEqual(tracks(result)["root"]["diagnostics"][0]["code"], "SPEC_IS_DIRECTORY")

    def test_directory_at_a_registered_spec_path_fails(self):
        with GitFixture() as fx:
            fx.commit_files({".docops-policy.json": policy_json(("docs/SPEC.md", "d")),
                             "docs/SPEC.md/a.txt": "x\n"})
            self.assertEqual(spec_status(check(fx, "HEAD")), "FAIL")

    def test_directory_at_the_policy_path_is_error_not_default_policy(self):
        with GitFixture() as fx:
            fx.commit_files({"SPEC.md": GOOD_SPEC, ".docops-policy.json/x": "x\n"})
            result = check(fx, "HEAD")
            self.assertToolError(result, "POLICY_INVALID")
            self.assertIn("directory", result["error"]["message"])

    def test_replace_refs_cannot_substitute_the_inspected_content(self):
        with GitFixture() as fx:
            good = fx.commit_files({"SPEC.md": GOOD_SPEC}, "good")
            bad = fx.commit_files({"SPEC.md": BAD_SPEC}, "bad")
            fx.git("replace", bad, good)  # `git show` of bad now yields good's content
            self.assertIn("good", fx.git("log", "-1", "--format=%s", bad))
            result = check(fx, bad)
            self.assertEqual(spec_status(result), "FAIL")
            self.assertEqual(result["snapshot"]["commit"], bad)

    def test_symlinked_spec_is_never_followed(self):
        with GitFixture() as fx:
            fx.write("real/SPEC.md", GOOD_SPEC)
            os.symlink("real/SPEC.md", os.path.join(fx.root, "SPEC.md"))
            fx.git("add", "-A")
            result = check(fx, "index")
            self.assertEqual(spec_status(result), "FAIL")
            self.assertEqual(tracks(result)["root"]["diagnostics"][0]["code"], "SPEC_NOT_REGULAR")

    def test_executable_bit_spec_is_a_regular_file(self):
        with GitFixture() as fx:
            fx.stage("SPEC.md", GOOD_SPEC)
            os.chmod(os.path.join(fx.root, "SPEC.md"), 0o755)
            fx.git("add", "SPEC.md")
            self.assertEqual(spec_status(check(fx, "index")), "PASS")

    def test_index_is_read_once_later_index_changes_cannot_leak_in(self):
        with GitFixture() as fx:
            fx.stage("SPEC.md", GOOD_SPEC)
            original = gitstate.Repository.read_blob
            calls = []

            def racing(repo, oid):
                if not calls:  # first blob read: someone restages a bad SPEC mid-run
                    calls.append(oid)
                    fx.stage("SPEC.md", BAD_SPEC)
                return original(repo, oid)

            with mock.patch.object(gitstate.Repository, "read_blob", racing):
                result = check(fx, "index")
            self.assertEqual(spec_status(result), "PASS")  # judged the listed (good) blob
            self.assertEqual(spec_status(check(fx, "index")), "FAIL")  # a new run sees the new index

    def test_result_is_independent_of_checkout_directory_name(self):
        with GitFixture() as fx:
            fx.commit_files({"SPEC.md": GOOD_SPEC, "component/x.py": "x = 1\n"})
            first = check(fx, "HEAD")["checks"]["SPEC_STRUCTURE"]
            renamed = os.path.join(os.path.dirname(fx.root), "totally-different-name")
            shutil.copytree(fx.root, renamed)
            try:
                repo = gitstate.open_repository(renamed)
                second = checks.check_request(repo, {"kind": "rev", "rev": "HEAD"})
                self.assertEqual(second["checks"]["SPEC_STRUCTURE"], first)
            finally:
                shutil.rmtree(renamed, ignore_errors=True)


class RegisteredTrackTests(DocopsTestCase):
    def test_two_active_tracks_pass_independently(self):
        with GitFixture() as fx:
            fx.commit_files({
                ".docops-policy.json": policy_json(("SPEC.md", "runner"),
                                                   ("verification/SPEC.md", "verification")),
                "SPEC.md": GOOD_SPEC, "verification/SPEC.md": SPEC_V2,
            })
            result = check(fx, "HEAD")
            self.assertEqual(spec_status(result), "PASS")
            self.assertEqual(sorted(tracks(result)), ["runner", "verification"])
            self.assertEqual(result["checks"]["SPEC_STRUCTURE"]["policy"]["source"], "snapshot")

    def test_one_bad_track_fails_the_run_and_is_named(self):
        with GitFixture() as fx:
            fx.commit_files({
                ".docops-policy.json": policy_json(("SPEC.md", "runner"),
                                                   ("verification/SPEC.md", "verification")),
                "SPEC.md": GOOD_SPEC, "verification/SPEC.md": BAD_SPEC,
            })
            result = check(fx, "HEAD")
            self.assertEqual(spec_status(result), "FAIL")
            self.assertEqual(tracks(result)["runner"]["status"], "PASS")
            self.assertEqual(tracks(result)["verification"]["status"], "FAIL")

    def test_good_track_does_not_hide_bad_track_and_vice_versa(self):
        with GitFixture() as fx:
            fx.commit_files({
                ".docops-policy.json": policy_json(("SPEC.md", "a"), ("b/SPEC.md", "b")),
                "SPEC.md": BAD_SPEC, "b/SPEC.md": GOOD_SPEC,
            })
            self.assertEqual(spec_status(check(fx, "HEAD")), "FAIL")

    def test_idle_and_active_tracks(self):
        with GitFixture() as fx:
            fx.commit_files({
                ".docops-policy.json": policy_json(("SPEC.md", "root"), ("v/SPEC.md", "v")),
                "SPEC.md": IDLE_SPEC, "v/SPEC.md": GOOD_SPEC,
            })
            result = check(fx, "HEAD")
            self.assertEqual(spec_status(result), "PASS")
            self.assertEqual((tracks(result)["root"]["status"], tracks(result)["root"]["state"]),
                             ("N/A", "idle"))

    def test_all_tracks_idle_is_na(self):
        with GitFixture() as fx:
            fx.commit_files({
                ".docops-policy.json": policy_json(("SPEC.md", "a"), ("v/SPEC.md", "b")),
                "SPEC.md": IDLE_SPEC, "v/SPEC.md": IDLE_SPEC,
            })
            self.assertEqual(spec_status(check(fx, "HEAD")), "N/A")

    def test_registered_but_missing_spec_fails_not_na(self):
        with GitFixture() as fx:
            fx.commit_files({".docops-policy.json": policy_json(("SPEC.md", "runner")),
                             "README.md": "x\n"})
            result = check(fx, "HEAD")
            self.assertEqual(spec_status(result), "FAIL")
            self.assertEqual(tracks(result)["runner"]["diagnostics"][0]["code"], "SPEC_MISSING")

    def test_deeply_nested_registered_spec_is_checked(self):
        with GitFixture() as fx:
            fx.commit_files({".docops-policy.json": policy_json(("a/b/c/SPEC.md", "deep")),
                             "a/b/c/SPEC.md": BAD_SPEC})
            self.assertEqual(spec_status(check(fx, "HEAD")), "FAIL")

    def test_index_uses_staged_policy_and_commit_uses_committed_policy(self):
        with GitFixture() as fx:
            fx.commit_files({".docops-policy.json": policy_json(("SPEC.md", "a")),
                             "SPEC.md": GOOD_SPEC, "other/SPEC.md": BAD_SPEC})
            fx.stage(".docops-policy.json", policy_json(("SPEC.md", "a"), ("other/SPEC.md", "o")))
            self.assertEqual(spec_status(check(fx, "HEAD")), "PASS")
            self.assertEqual(spec_status(check(fx, "index")), "FAIL")

    def test_untracked_policy_on_disk_is_ignored(self):
        with GitFixture() as fx:
            fx.commit_files({"SPEC.md": GOOD_SPEC, "other/SPEC.md": BAD_SPEC})
            fx.write(".docops-policy.json", policy_json(("other/SPEC.md", "o")))
            result = check(fx, "index")
            self.assertEqual(result["checks"]["SPEC_STRUCTURE"]["policy"]["source"], "default")

    def test_invalid_policy_is_error_not_default(self):
        with GitFixture() as fx:
            fx.commit_files({"SPEC.md": GOOD_SPEC, ".docops-policy.json": "{not json"})
            result = check(fx, "HEAD")
            self.assertToolError(result, "POLICY_INVALID")

    def test_symlinked_policy_is_error(self):
        with GitFixture() as fx:
            fx.write("real.json", policy_json(("SPEC.md", "a")))
            os.symlink("real.json", os.path.join(fx.root, ".docops-policy.json"))
            fx.stage("SPEC.md", GOOD_SPEC)
            fx.git("add", "-A")
            self.assertToolError(check(fx, "index"))


class ScopeTests(DocopsTestCase):
    def test_nested_spec_beside_good_root_is_reported_not_silently_skipped(self):
        with GitFixture() as fx:
            fx.commit_files({"SPEC.md": GOOD_SPEC, "a/b/SPEC.md": BAD_SPEC})
            check_ = check(fx, "HEAD")["checks"]["SPEC_STRUCTURE"]
            self.assertEqual(check_["status"], "PASS")
            self.assertEqual([d["code"] for d in check_["diagnostics"]], ["OUT_OF_SCOPE_SPEC"])
            self.assertIn("a/b/SPEC.md", check_["diagnostics"][0]["message"])

    def test_idle_root_does_not_hide_a_broken_unregistered_spec(self):
        with GitFixture() as fx:
            fx.commit_files({"SPEC.md": IDLE_SPEC, "docs/x/SPEC.md": "# open\n\n- [ ] open item\n"})
            result = check(fx, "HEAD")
            self.assertEqual(spec_status(result), "SKIP")
            self.assertEqual(result["exit_code"], 3)
            self.assertEqual(tracks(result)["root"]["status"], "N/A")  # still reported per track

    def test_only_a_nested_spec_is_skip_never_na(self):
        with GitFixture() as fx:
            fx.commit_files({"deep/er/SPEC.md": GOOD_SPEC})
            result = check(fx, "HEAD")
            self.assertEqual(spec_status(result), "SKIP")
            self.assertEqual(result["exit_code"], 3)
            self.assertEqual(result["checks"]["SPEC_STRUCTURE"]["tracks"], [])

    def test_one_level_child_spec_needs_registration(self):
        with GitFixture() as fx:
            fx.commit_files({"verification/SPEC.md": BAD_SPEC})
            self.assertEqual(spec_status(check(fx, "HEAD")), "SKIP")
            fx.commit_files({".docops-policy.json": policy_json(("verification/SPEC.md", "v"))})
            self.assertEqual(spec_status(check(fx, "HEAD")), "FAIL")  # now in scope, and bad

    def test_empty_registry_with_a_spec_present_is_skip(self):
        with GitFixture() as fx:
            fx.commit_files({".docops-policy.json": policy_json(), "SPEC.md": IDLE_SPEC})
            self.assertEqual(spec_status(check(fx, "HEAD")), "SKIP")

    def test_empty_registry_without_any_spec_is_na(self):
        with GitFixture() as fx:
            fx.commit_files({".docops-policy.json": policy_json(), "README.md": "x\n"})
            self.assertEqual(spec_status(check(fx, "HEAD")), "N/A")

    def test_lowercase_spec_name_is_also_reported(self):
        with GitFixture() as fx:
            fx.commit_files({"docs/spec.md": GOOD_SPEC})
            self.assertEqual(spec_status(check(fx, "HEAD")), "SKIP")


class CheckerFailureTests(DocopsTestCase):
    """The checker's own failure modes must never come out as PASS or N/A."""

    def setUp(self):
        self.fx = GitFixture()
        self.addCleanup(self.fx.cleanup)
        self.fx.commit_files({"SPEC.md": GOOD_SPEC})

    def assertError(self, result, code=None):
        self.assertToolError(result, code)
        self.assertNeverPassOrNa(result["status"])

    def test_internal_error_in_parser_is_error(self):
        with mock.patch.object(specparse, "analyze_spec", side_effect=RuntimeError("boom")):
            result = check(self.fx, "HEAD")
        self.assertError(result, "INTERNAL_ERROR")
        self.assertIn("RuntimeError", result["error"]["message"])

    def test_internal_error_in_snapshot_reader_is_error(self):
        with mock.patch.object(gitstate, "index_snapshot", side_effect=ValueError("bad")):
            self.assertError(check(self.fx, "index"))

    def test_unreadable_blob_is_error(self):
        with mock.patch.object(gitstate.Repository, "read_blob", side_effect=GitError("gone")):
            result = check(self.fx, "HEAD")
        self.assertError(result, "BLOB_UNREADABLE")

    def test_missing_object_in_real_repository_is_error(self):
        blob = self.fx.git("rev-parse", "HEAD:SPEC.md")
        loose = os.path.join(self.fx.root, ".git", "objects", blob[:2], blob[2:])
        self.assertTrue(os.path.exists(loose))
        os.remove(loose)  # the SPEC blob vanishes; the tree entry still names it
        result = check(self.fx, "HEAD")
        self.assertError(result, "BLOB_UNREADABLE")

    def test_not_a_git_repository(self):
        with tempfile.TemporaryDirectory() as plain:
            with self.assertRaises(GitError):
                gitstate.open_repository(plain)

    def test_nonexistent_repository_path(self):
        with self.assertRaises(GitError):
            gitstate.open_repository("/nonexistent/definitely/not/here")

    def test_git_executable_missing(self):
        with tempfile.TemporaryDirectory() as empty, \
                mock.patch.dict(os.environ, {"PATH": empty}):
            with self.assertRaises(GitError) as ctx:
                gitstate.open_repository(self.fx.root)
        self.assertIn("not found", str(ctx.exception))

    def test_git_that_hangs_times_out_as_error(self):
        with tempfile.TemporaryDirectory() as bindir:
            fake = os.path.join(bindir, "git")
            with open(fake, "w") as handle:
                handle.write("#!/bin/sh\nexec /bin/sleep 5\n")
            os.chmod(fake, 0o755)
            with mock.patch.dict(os.environ, {"PATH": bindir}), \
                    mock.patch.object(gitstate, "GIT_TIMEOUT_SEC", 0.3):
                with self.assertRaises(GitError) as ctx:
                    gitstate.open_repository(self.fx.root)
        self.assertIn("timed out", str(ctx.exception))

    def test_git_that_fails_mid_run_is_error_result(self):
        real_run = gitstate._run

        def flaky(cwd, args):
            if args and args[0] == "ls-tree":
                raise GitError("simulated failure")
            return real_run(cwd, args)

        with mock.patch.object(gitstate, "_run", flaky):
            self.assertError(check(self.fx, "HEAD"))

    def test_git_environment_forbids_locks_and_lazy_fetch(self):
        seen = []
        real = gitstate.subprocess.run

        def spy(*args, **kwargs):
            seen.append(kwargs["env"])
            return real(*args, **kwargs)

        with mock.patch.object(gitstate.subprocess, "run", spy):
            check(self.fx, "index")
            check(self.fx, "HEAD")
        self.assertTrue(seen)
        for env in seen:
            self.assertEqual(env["GIT_OPTIONAL_LOCKS"], "0")
            self.assertEqual(env["GIT_NO_LAZY_FETCH"], "1")
            self.assertEqual(env["GIT_NO_REPLACE_OBJECTS"], "1")
            self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")

    def test_checker_never_opens_a_socket(self):
        import socket

        def forbidden(*args, **kwargs):
            raise AssertionError("network access attempted")

        with mock.patch.object(socket.socket, "connect", forbidden), \
                mock.patch.object(socket, "create_connection", forbidden):
            self.assertEqual(spec_status(check(self.fx, "HEAD")), "PASS")

    def test_non_evaluated_dimensions_can_never_be_pass(self):
        result = check(self.fx, "HEAD")
        for name in ("TESTS", "DOC_IMPACT_ASSESSMENT", "DOC_CONSISTENCY_REVIEW",
                     "ACCEPTANCE_VERIFICATION", "PUBLICATION", "OWNER_ACCEPTANCE"):
            self.assertEqual(result["checks"][name], {
                "assessed": False, "reason": result["checks"][name]["reason"]}, name)
            self.assertNotIn("status", result["checks"][name], name)


class AggregationTests(DocopsTestCase):
    def test_precedence(self):
        agg = checks.aggregate
        self.assertEqual(agg(["PASS", "N/A"]), "PASS")
        self.assertEqual(agg(["N/A", "N/A"]), "N/A")
        self.assertEqual(agg(["PASS", "SKIP"]), "SKIP")
        self.assertEqual(agg(["SKIP", "FAIL"]), "FAIL")
        self.assertEqual(agg(["FAIL", "PASS", "N/A"]), "FAIL")
        self.assertEqual(agg([]), "N/A")  # callers must not rely on this; build_result guards

    def test_unassessed_dimensions_carry_no_status_and_the_schema_says_so(self):
        schema = load_result_schema()
        not_assessed = schema["$defs"]["notAssessed"]
        self.assertEqual(sorted(not_assessed["properties"]), ["assessed", "reason"])
        self.assertEqual(not_assessed["properties"]["assessed"], {"const": False})
        self.assertNotIn("status", not_assessed["properties"])
        self.assertNotIn("notEvaluated", schema["$defs"])
        self.assertNotIn("NOT_EVALUATED", json.dumps(schema))
        self.assertTrue(not_assessed["additionalProperties"] is False)
        dimensions = schema["$defs"]["snapshotResult"]["properties"]["checks"]["anyOf"][1]["properties"]
        for name in ("TESTS", "DOC_CONSISTENCY_REVIEW", "ACCEPTANCE_VERIFICATION",
                     "PUBLICATION", "OWNER_ACCEPTANCE"):
            self.assertEqual(dimensions[name], {"$ref": "#/$defs/notAssessed"}, name)
        # ADR-0014: only DOC_IMPACT_ASSESSMENT may also be assessed, and then never N/A.
        self.assertEqual(dimensions["DOC_IMPACT_ASSESSMENT"], {"anyOf": [
            {"$ref": "#/$defs/notAssessed"}, {"$ref": "#/$defs/impactAssessment"}]})
        self.assertEqual(schema["$defs"]["impactAssessment"]["properties"]["status"]["enum"],
                         ["PASS", "FAIL", "SKIP"])
        # a document that gives such a dimension a status is invalid
        good = {"schema_version": 1, "tool": {"name": "docops", "version": "x"}, "command": "check",
                "repository": {"root": "/r"}, "snapshots": [], "summary": {
                    "status": None, "exit_code": 2, "tool_errors": 1, "reason": "x"}}
        self.assertEqual(validate(good, schema), [])
        snap = checks.deleted_snapshot_result({"kind": "deleted-ref"})
        ok = dict(good, snapshots=[snap], summary={"status": "N/A", "exit_code": 0, "tool_errors": 0})
        self.assertEqual(validate(ok, schema), [])
        snap["checks"]["TESTS"] = {"status": "NOT_EVALUATED", "reason": "x"}
        self.assertNotEqual(validate(ok, schema), [])
        snap["checks"]["TESTS"] = {"assessed": False, "reason": "x", "status": "PASS"}
        self.assertNotEqual(validate(ok, schema), [])

    def test_there_is_no_fifth_status(self):
        # The per-check vocabulary is exactly PASS, FAIL, SKIP, N/A.
        self.assertEqual(sorted(checks._PRECEDENCE), ["FAIL", "N/A", "PASS", "SKIP"])
        self.assertFalse(hasattr(checks, "ERROR"))
        schema = load_result_schema()
        self.assertEqual(sorted(schema["$defs"]["specStatus"]["enum"]), ["FAIL", "N/A", "PASS", "SKIP"])

    def test_empty_run_is_tool_error_not_na(self):
        result = checks.build_result("check", "/x", [])
        self.assertEqual((result["summary"]["status"], result["summary"]["exit_code"]), (None, 2))

    def test_any_tool_error_voids_the_summary_verdict(self):
        good = checks.snapshot_result({"kind": "index"}, {"kind": "index", "commit": None,
                                      "tree": None, "index_digest": "x"},
                                      {"status": "PASS", "reason": "r", "policy": {}, "tracks": [],
                                       "diagnostics": []})
        broken = checks.error_snapshot_result({"kind": "rev", "rev": "x"}, "SNAPSHOT_UNREADABLE", "m")
        result = checks.build_result("check", "/x", [good, broken])
        self.assertEqual((result["summary"]["status"], result["summary"]["exit_code"],
                          result["summary"]["tool_errors"]), (None, 2, 1))

    def test_exit_code_map(self):
        self.assertEqual([checks.exit_code_for(s) for s in ("PASS", "N/A", "FAIL", "SKIP")],
                         [0, 0, 1, 3])


if __name__ == "__main__":
    unittest.main()
