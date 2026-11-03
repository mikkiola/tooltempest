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


def policy_doc(specs=(), non_active=None):
    """A policy document; `non_active` is the raw `non_active_specs` value (None: key absent)."""
    document = {"schema_version": 1, "specs": [{"path": p, "track": t} for p, t in specs]}
    if non_active is not None:
        document["non_active_specs"] = non_active
    return json.dumps(document)


def listed(path, reason="Not a project specification."):
    return {"path": path, "reason": reason}


def finding_codes(result):
    return [d["code"] for d in result["checks"]["SPEC_STRUCTURE"]["diagnostics"]]


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
            fx.commit_files({".docops-policy.json": policy_doc([("SPEC.md", "a")],
                                                                [listed("other/SPEC.md")]),
                             "SPEC.md": GOOD_SPEC, "other/SPEC.md": BAD_SPEC})
            fx.stage(".docops-policy.json", policy_json(("SPEC.md", "a"), ("other/SPEC.md", "o")))
            self.assertEqual(spec_status(check(fx, "HEAD")), "PASS")   # committed: listed non-active
            self.assertEqual(spec_status(check(fx, "index")), "FAIL")  # staged: an active, broken track

    def test_untracked_policy_on_disk_is_ignored(self):
        with GitFixture() as fx:
            fx.commit_files({"SPEC.md": GOOD_SPEC, "other/SPEC.md": BAD_SPEC})
            fx.write(".docops-policy.json", policy_doc(non_active=[listed("other/SPEC.md")]))
            result = check(fx, "index")
            self.assertEqual(result["checks"]["SPEC_STRUCTURE"]["policy"]["source"], "default")
            self.assertEqual(spec_status(result), "FAIL")  # the untracked listing does not count

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


class ClassificationTests(DocopsTestCase):
    """ADR-0013 Decision 7 (amended): the root SPEC.md is always evaluated, and every file named
    SPEC.md is exactly one of root / registered active / listed non-active, or the check FAILs."""

    def assertFails(self, files, code=None, tree="HEAD"):
        with GitFixture() as fx:
            fx.commit_files(files)
            result = check(fx, tree)
            self.assertEqual(spec_status(result), "FAIL", result["checks"])
            self.assertEqual(result["exit_code"], 1)
            if code is not None:
                self.assertIn(code, finding_codes(result))
            return result

    # -- the root SPEC ---------------------------------------------------------------------------
    def test_a_root_spec_alone_passes_with_and_without_a_policy(self):
        for extra in ({}, {".docops-policy.json": policy_doc()},
                      {".docops-policy.json": policy_doc(non_active=[])}):
            with GitFixture() as fx:
                fx.commit_files({"SPEC.md": GOOD_SPEC, **extra})
                result = check(fx, "HEAD")
                self.assertEqual(spec_status(result), "PASS", extra)
                self.assertEqual([t["track"] for t in result["checks"]["SPEC_STRUCTURE"]["tracks"]],
                                 ["root"])
                self.assertEqual(finding_codes(result), [])

    def test_a_policy_that_omits_the_root_still_evaluates_it(self):
        # before the amendment a policy registering only another SPEC dropped the root
        with GitFixture() as fx:
            fx.commit_files({".docops-policy.json": policy_json(("v/SPEC.md", "v")),
                             "SPEC.md": BAD_SPEC, "v/SPEC.md": GOOD_SPEC})
            result = check(fx, "HEAD")
            self.assertEqual(spec_status(result), "FAIL")
            self.assertEqual(tracks(result)["root"]["status"], "FAIL")
            self.assertEqual(tracks(result)["v"]["status"], "PASS")

    def test_an_explicit_root_entry_is_the_root_evaluated_once_without_duplicate_findings(self):
        for name in ("root", "runner"):
            with GitFixture() as fx:
                fx.commit_files({".docops-policy.json": policy_json(("SPEC.md", name)),
                                 "SPEC.md": GOOD_SPEC})
                result = check(fx, "HEAD")
                self.assertEqual(spec_status(result), "PASS", name)
                self.assertEqual([t["track"] for t in result["checks"]["SPEC_STRUCTURE"]["tracks"]],
                                 [name])
                self.assertEqual(finding_codes(result), [])
        with GitFixture() as fx:  # a broken root named explicitly yields one failing track only
            fx.commit_files({".docops-policy.json": policy_json(("SPEC.md", "root")),
                             "SPEC.md": BAD_SPEC})
            result = check(fx, "HEAD")
            self.assertEqual(len(result["checks"]["SPEC_STRUCTURE"]["tracks"]), 1)
            self.assertEqual(finding_codes(result), [])

    def test_an_absent_root_is_unchanged_by_this_rule(self):
        with GitFixture() as fx:  # no policy, no root: N/A as before
            fx.commit_files({"README.md": "x\n"})
            self.assertEqual(spec_status(check(fx, "HEAD")), "N/A")
        with GitFixture() as fx:  # a policy that does not name the root: still N/A
            fx.commit_files({".docops-policy.json": policy_doc(), "README.md": "x\n"})
            self.assertEqual(spec_status(check(fx, "HEAD")), "N/A")
        with GitFixture() as fx:  # a policy that names the absent root: FAIL, as for any registered path
            fx.commit_files({".docops-policy.json": policy_json(("SPEC.md", "root")),
                             "README.md": "x\n"})
            result = check(fx, "HEAD")
            self.assertEqual(spec_status(result), "FAIL")
            self.assertEqual(tracks(result)["root"]["diagnostics"][0]["code"], "SPEC_MISSING")

    # -- unclassified files ----------------------------------------------------------------------
    def test_a_root_plus_an_unlisted_nested_spec_fails_and_names_the_path_and_rule(self):
        result = self.assertFails({"SPEC.md": GOOD_SPEC, "a/b/SPEC.md": GOOD_SPEC},
                                  "OUT_OF_SCOPE_SPEC")
        message = result["checks"]["SPEC_STRUCTURE"]["diagnostics"][0]["message"]
        self.assertIn("a/b/SPEC.md", message)
        self.assertIn("non_active_specs", message)
        self.assertEqual(tracks(result)["root"]["status"], "PASS")  # the root is still evaluated

    def test_an_idle_root_does_not_hide_an_unlisted_spec(self):
        self.assertFails({"SPEC.md": IDLE_SPEC, "docs/x/SPEC.md": "# open\n\n- [ ] open item\n"},
                         "OUT_OF_SCOPE_SPEC")

    def test_only_a_nested_spec_fails_never_na_and_never_skip(self):
        result = self.assertFails({"deep/er/SPEC.md": GOOD_SPEC}, "OUT_OF_SCOPE_SPEC")
        self.assertEqual(result["checks"]["SPEC_STRUCTURE"]["tracks"], [])

    def test_the_name_is_matched_case_insensitively_at_any_depth_and_other_names_are_not(self):
        for path in ("docs/spec.md", "docs/Spec.MD", "a/b/c/d/SPEC.md", "spec.md"):
            with self.subTest(path):
                self.assertFails({path: GOOD_SPEC}, "OUT_OF_SCOPE_SPEC")
        with GitFixture() as fx:
            fx.commit_files({"SPEC.txt": "x\n", "docs/SPECIFICATION.md": "x\n"})
            self.assertEqual(spec_status(check(fx, "HEAD")), "N/A")

    def test_a_registered_nested_spec_needs_no_listing(self):
        with GitFixture() as fx:
            fx.commit_files({"verification/SPEC.md": BAD_SPEC})
            self.assertEqual(spec_status(check(fx, "HEAD")), "FAIL")  # unclassified
            fx.commit_files({".docops-policy.json": policy_json(("verification/SPEC.md", "v")),
                             "verification/SPEC.md": GOOD_SPEC})
            self.assertEqual(spec_status(check(fx, "HEAD")), "PASS")

    # -- explicitly listed non-active files ------------------------------------------------------
    def test_a_nested_spec_listed_as_non_active_passes_and_is_reported(self):
        with GitFixture() as fx:
            fx.commit_files({"SPEC.md": GOOD_SPEC, "docs/old/SPEC.md": BAD_SPEC,
                             ".docops-policy.json": policy_doc(
                                 non_active=[listed("docs/old/SPEC.md", "Historical material.")])})
            result = check(fx, "HEAD")
            structure = result["checks"]["SPEC_STRUCTURE"]
            self.assertEqual(spec_status(result), "PASS")
            self.assertEqual(structure["non_active_specs"],
                             [{"path": "docs/old/SPEC.md", "reason": "Historical material."}])
            self.assertEqual(structure["diagnostics"], [])
            self.assertEqual([t["track"] for t in structure["tracks"]], ["root"])  # not evaluated

    def test_a_classified_fixture_passes(self):
        with GitFixture() as fx:
            fx.commit_files({"SPEC.md": GOOD_SPEC, "tests/fixtures/SPEC.md": "not a spec\n",
                             ".docops-policy.json": policy_doc(
                                 non_active=[listed("tests/fixtures/SPEC.md",
                                                    "Fixture used by checker tests; "
                                                    "not a project specification.")])})
            self.assertEqual(spec_status(check(fx, "HEAD")), "PASS")

    def test_a_policy_with_only_non_active_entries_does_not_skip(self):
        fixture_policy = policy_doc(non_active=[listed("tests/fixtures/SPEC.md")])
        with GitFixture() as fx:  # no root: N/A, exactly as without a policy, never SKIP
            fx.commit_files({"tests/fixtures/SPEC.md": "x\n", ".docops-policy.json": fixture_policy})
            result = check(fx, "HEAD")
            self.assertEqual((spec_status(result), result["exit_code"]), ("N/A", 0))
        with GitFixture() as fx:  # with a root: evaluated as without a policy
            fx.commit_files({"SPEC.md": GOOD_SPEC, "tests/fixtures/SPEC.md": "x\n",
                             ".docops-policy.json": fixture_policy})
            self.assertEqual(spec_status(check(fx, "HEAD")), "PASS")
        with GitFixture() as fx:
            fx.commit_files({"SPEC.md": IDLE_SPEC, "tests/fixtures/SPEC.md": "x\n",
                             ".docops-policy.json": fixture_policy})
            self.assertEqual(spec_status(check(fx, "HEAD")), "N/A")

    # -- invalid, conflicting and stale classifications -----------------------------------------
    def test_a_path_that_is_both_active_and_non_active_fails(self):
        self.assertFails({"SPEC.md": GOOD_SPEC, "v/SPEC.md": GOOD_SPEC,
                          ".docops-policy.json": policy_doc([("v/SPEC.md", "v")],
                                                            [listed("v/SPEC.md")])},
                         "SPEC_CLASSIFICATION_CONFLICT")

    def test_a_duplicate_non_active_entry_fails(self):
        result = self.assertFails({"SPEC.md": GOOD_SPEC, "f/SPEC.md": "x\n",
                                   ".docops-policy.json": policy_doc(
                                       non_active=[listed("f/SPEC.md"), listed("f/SPEC.md", "again")])},
                                  "NON_ACTIVE_DUPLICATE")
        self.assertEqual(finding_codes(result).count("NON_ACTIVE_DUPLICATE"), 1)

    def test_a_non_active_entry_without_a_reason_fails(self):
        for entry in ({"path": "f/SPEC.md"}, {"path": "f/SPEC.md", "reason": ""},
                      {"path": "f/SPEC.md", "reason": "   \t"}):
            with self.subTest(entry):
                self.assertFails({"SPEC.md": GOOD_SPEC, "f/SPEC.md": "x\n",
                                  ".docops-policy.json": policy_doc(non_active=[entry])},
                                 "NON_ACTIVE_REASON_MISSING")

    def test_a_stale_non_active_entry_fails(self):
        self.assertFails({"SPEC.md": GOOD_SPEC,
                          ".docops-policy.json": policy_doc(non_active=[listed("gone/SPEC.md")])},
                         "NON_ACTIVE_STALE")

    def test_the_root_listed_as_non_active_fails(self):
        self.assertFails({"SPEC.md": GOOD_SPEC,
                          ".docops-policy.json": policy_doc(non_active=[listed("SPEC.md")])},
                         "ROOT_LISTED_NON_ACTIVE")
        self.assertFails({"SPEC.md": GOOD_SPEC, ".docops-policy.json": policy_doc(
            [("SPEC.md", "root")], [listed("SPEC.md")])}, "ROOT_LISTED_NON_ACTIVE")

    def test_directory_prefix_entries_are_not_supported_and_hide_nothing(self):
        result = self.assertFails({"SPEC.md": GOOD_SPEC, "tests/fixtures/SPEC.md": "x\n",
                                   ".docops-policy.json": policy_doc(
                                       non_active=[listed("tests/fixtures")])},
                                  "NON_ACTIVE_NOT_A_FILE")
        self.assertIn("OUT_OF_SCOPE_SPEC", finding_codes(result))

    def test_a_broken_listing_leaves_the_file_unclassified_never_a_pass(self):
        cases = {
            "no reason": ([{"path": "f/SPEC.md"}], "NON_ACTIVE_REASON_MISSING"),
            "duplicate": ([listed("f/SPEC.md"), listed("f/SPEC.md")], "NON_ACTIVE_DUPLICATE"),
        }
        for name, (entries, code) in cases.items():
            with self.subTest(name):
                self.assertFails({"SPEC.md": GOOD_SPEC, "f/SPEC.md": "x\n",
                                  ".docops-policy.json": policy_doc(non_active=entries)}, code)

    def test_a_nested_symlink_named_spec_md_is_unclassified_never_followed(self):
        with GitFixture() as fx:
            fx.write("real.md", GOOD_SPEC)
            os.makedirs(os.path.join(fx.root, "nested"))
            os.symlink("../real.md", os.path.join(fx.root, "nested", "SPEC.md"))
            fx.stage("SPEC.md", GOOD_SPEC)
            fx.git("add", "-A")
            fx.git("commit", "-q", "-m", "symlink")
            result = check(fx, "HEAD")
            self.assertEqual(spec_status(result), "FAIL")
            self.assertIn("OUT_OF_SCOPE_SPEC", finding_codes(result))

    def test_a_track_named_root_elsewhere_does_not_share_the_name_with_the_implicit_root(self):
        files = {".docops-policy.json": policy_json(("v/SPEC.md", "root")),
                 "SPEC.md": GOOD_SPEC, "v/SPEC.md": GOOD_SPEC}
        self.assertFails(files, "SPEC_CLASSIFICATION_CONFLICT")
        with GitFixture() as fx:  # no root SPEC.md: only one track carries the name, no conflict
            fx.commit_files({".docops-policy.json": policy_json(("v/SPEC.md", "root")),
                             "v/SPEC.md": GOOD_SPEC})
            self.assertEqual(spec_status(check(fx, "HEAD")), "PASS")

    def test_a_malformed_policy_shape_is_a_tool_error_not_a_verdict(self):
        bad = ({}, {"path": "x"}, "text", [1], [{"path": "f/SPEC.md", "reason": "r", "extra": 1}],
               [{"reason": "r"}], [{"path": "../SPEC.md", "reason": "r"}],
               [{"path": "f/SPEC.md", "reason": 5}])
        for value in bad:
            with self.subTest(value):
                with GitFixture() as fx:
                    fx.commit_files({"SPEC.md": GOOD_SPEC,
                                     ".docops-policy.json": policy_doc(non_active=value)})
                    self.assertToolError(check(fx, "HEAD"), "POLICY_INVALID")

    def test_a_policy_without_the_new_field_stays_valid_and_behaves_as_before(self):
        with GitFixture() as fx:  # a Brain-shaped policy: root and one more track, both registered
            fx.commit_files({".docops-policy.json": policy_json(("SPEC.md", "root"),
                                                                ("verification/SPEC.md", "verification")),
                             "SPEC.md": GOOD_SPEC, "verification/SPEC.md": SPEC_V2})
            result = check(fx, "HEAD")
            self.assertEqual(spec_status(result), "PASS")
            self.assertEqual([t["track"] for t in result["checks"]["SPEC_STRUCTURE"]["tracks"]],
                             ["root", "verification"])
            self.assertEqual(finding_codes(result), [])
            self.assertEqual(result["checks"]["SPEC_STRUCTURE"]["non_active_specs"], [])

    def test_results_with_classification_fields_conform_to_the_schema(self):
        with GitFixture() as fx:
            fx.commit_files({"SPEC.md": GOOD_SPEC, "f/SPEC.md": "x\n", "g/SPEC.md": "x\n",
                             ".docops-policy.json": policy_doc(non_active=[listed("f/SPEC.md")])})
            result = check(fx, "HEAD")
            document = {"schema_version": 1, "tool": {"name": "docops", "version": "0.3.0"},
                        "command": "check", "repository": {"root": fx.root}, "snapshots": [result],
                        "summary": {"status": result["status"], "exit_code": result["exit_code"],
                                    "tool_errors": 0}}
            self.assertEqual(validate(document, load_result_schema()), [])


class ClassificationParityTests(DocopsTestCase):
    """The impact validator's SPEC candidates and the checker classify the same cases the same way."""

    CASES = {
        "root only": {"SPEC.md": GOOD_SPEC},
        "root and unlisted nested": {"SPEC.md": GOOD_SPEC, "a/SPEC.md": GOOD_SPEC},
        "nested only, unlisted": {"a/b/spec.md": GOOD_SPEC},
        "registered track": {".docops-policy.json": policy_json(("v/SPEC.md", "v")),
                             "v/SPEC.md": GOOD_SPEC},
        "policy omits root": {".docops-policy.json": policy_json(("v/SPEC.md", "v")),
                              "SPEC.md": GOOD_SPEC, "v/SPEC.md": GOOD_SPEC},
        "explicit root": {".docops-policy.json": policy_json(("SPEC.md", "root")),
                          "SPEC.md": GOOD_SPEC},
        "listed non-active": {".docops-policy.json": policy_doc(non_active=[listed("f/SPEC.md")]),
                              "f/SPEC.md": "x\n"},
        "listed without reason": {".docops-policy.json": policy_doc(non_active=[{"path": "f/SPEC.md"}]),
                                  "f/SPEC.md": "x\n"},
        "active and non-active": {".docops-policy.json": policy_doc([("v/SPEC.md", "v")],
                                                                    [listed("v/SPEC.md")]),
                                  "v/SPEC.md": GOOD_SPEC},
        "root listed non-active": {".docops-policy.json": policy_doc(non_active=[listed("SPEC.md")]),
                                   "SPEC.md": GOOD_SPEC},
        "stale entry beside an unlisted file": {
            ".docops-policy.json": policy_doc(non_active=[listed("gone/SPEC.md")]),
            "u/SPEC.md": "x\n"},
        "other names": {"SPEC.txt": "x\n", "docs/SPECIFICATION.md": "x\n"},
    }

    def test_candidates_equal_what_the_checker_accepted_or_flagged(self):
        for name, files in self.CASES.items():
            with self.subTest(name):
                with GitFixture() as fx:
                    fx.commit_files(files)
                    repo = gitstate.open_repository(fx.root)
                    snapshot = gitstate.commit_snapshot(repo, "HEAD")
                    candidates = checks.spec_candidate_paths(snapshot)
                    structure = check(fx, "HEAD")["checks"]["SPEC_STRUCTURE"]
                    self.assertEqual(len(candidates), len(set(candidates)), "a path listed twice")
                    self.assertEqual(candidates[0], "SPEC.md")  # the root, always, once
                    named = {p for p in files if p.lower().endswith("spec.md")
                             and p.rsplit("/", 1)[-1].lower() == "spec.md"}
                    accepted = {t["path"] for t in structure["tracks"]}
                    accepted |= {n["path"] for n in structure["non_active_specs"]}
                    # candidates = the root + every checker track + every SPEC-named file that the
                    # checker did not accept as a track or a validly listed non-active file
                    expected = {"SPEC.md"} | {t["path"] for t in structure["tracks"]} | (named - accepted)
                    self.assertEqual(set(candidates), expected)
                    # and a SPEC-named file the checker flags is never absent from the candidates
                    for diagnostic in structure["diagnostics"]:
                        if diagnostic["code"] == "OUT_OF_SCOPE_SPEC":
                            self.assertTrue(any(p in diagnostic["message"] for p in candidates))


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
