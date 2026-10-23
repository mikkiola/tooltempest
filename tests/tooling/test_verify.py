"""verify / status: the three properties, kept apart; read-only; offline.

Proven here: each property can fail while the others hold; verify exits 0
only when local integrity and upstream provenance are both established;
freshness is advisory and `unknown` unless a local ref says otherwise;
verify and status write nothing and use no network; every unestablished or
broken input is exit 1 with a finding (exit 2 is reserved for the tool being
unable to run).
"""
import json
import os
import shutil
import subprocess
import sys
import zlib

from tooling_helpers import (NO_REF, REPO_ROOT, TOOLING_PY, UNREACHABLE_URL, Consumer, ToolingCase,
                             Upstream, clean_env, engine, run_tooling, sha256)


class ThreePropertiesTests(ToolingCase):
    def test_a_clean_installation_is_established_on_every_property(self):
        self.publish_v1()
        self.apply()
        code, doc = self.verify()
        self.assertEqual(code, 0)
        self.assertEqual((doc["summary"]["local_integrity"], doc["summary"]["upstream_provenance"],
                          doc["summary"]["freshness"]), ("ok", "ok", "current"))
        self.assertEqual(self.codes(doc, "error"), [])
        for record in doc["files"]:
            self.assertEqual((record["local"]["status"], record["provenance"], record["matches_pin"]),
                             ("ok", "verified", True), record["path"])

    def test_local_integrity_can_fail_while_provenance_holds(self):
        self.publish_v1()
        self.apply()
        self.consumer.write("scripts/alpha.py", "print('edited')\n")
        code, doc = self.verify()
        self.assertEqual(code, 1)
        self.assertEqual((doc["summary"]["local_integrity"], doc["summary"]["upstream_provenance"]),
                         ("failed", "ok"))
        self.assertEqual(self.file(doc, "scripts/alpha.py")["local"]["status"], "modified")
        self.assertIn("LOCAL_MODIFIED", self.codes(doc, "error"))

    def test_provenance_can_fail_while_local_integrity_holds(self):
        # the installed bytes match what the lock declares, but the lock's declaration is not the
        # blob at the pin: the installation is faithful to a wrong declaration
        commit = self.publish_v1()
        wrong = b"print('not the blob')\n"
        self.consumer.write("scripts/alpha.py", wrong)
        self.consumer.write_lock(commit, declared={"scripts/alpha.py": sha256(wrong)})
        code, doc = self.verify()
        self.assertEqual(code, 1)
        record = self.file(doc, "scripts/alpha.py")
        self.assertEqual((record["local"]["status"], record["provenance"]), ("ok", "mismatch"))
        self.assertEqual(doc["summary"]["upstream_provenance"], "failed")
        self.assertIn("PROVENANCE_MISMATCH", self.codes(doc, "error"))

    def test_freshness_is_reported_apart_and_never_changes_the_exit_code(self):
        self.publish_v1()
        self.apply()
        self.publish_v2()
        code, doc = self.verify()
        self.assertEqual(code, 0)  # still at the pin: integrity and provenance are fine
        self.assertEqual(doc["summary"]["freshness"], "behind")
        self.assertEqual(doc["freshness"]["behind"], 1)
        self.assertEqual(doc["freshness"]["upstream_commit"], self.commit2)
        self.assertIn("PIN_BEHIND", self.codes(doc, "warning"))

    def test_freshness_is_unknown_when_no_ref_can_say_and_nothing_is_fetched(self):
        self.publish_v1()
        self.apply()
        code, doc = self.verify("--upstream-ref", NO_REF)
        self.assertEqual(code, 0)
        self.assertEqual(doc["summary"]["freshness"], "unknown")
        self.assertIn("nothing was fetched", doc["freshness"]["reason"])
        self.assertIn("FRESHNESS_UNKNOWN", self.codes(doc, "info"))

    def test_a_pin_off_the_upstream_ref_is_not_called_current_or_behind(self):
        self.publish_v1()
        self.upstream.fx.git("checkout", "-q", "-b", "side")
        side = self.upstream.publish({"scripts/alpha.py": "side\n"}, message="side")
        self.upstream.fx.git("checkout", "-q", "main")
        self.upstream.publish({"scripts/alpha.py": "main2\n"}, message="main2")
        self.consumer.write_lock(side)
        self.consumer.write("scripts/alpha.py", "side\n")
        code, doc = self.verify("--upstream-ref", "refs/heads/main")
        self.assertEqual(doc["summary"]["freshness"], "not_on_ref")

    def test_fresh_pin_reports_current(self):
        self.publish_v1()
        code, doc = self.verify("--upstream-ref", "refs/heads/main")
        self.assertEqual(doc["summary"]["freshness"], "current")


class StatusTests(ToolingCase):
    def test_status_reports_the_same_properties_but_exits_zero_on_findings(self):
        self.publish_v1()
        code, doc = self.run_json("status")
        self.assertEqual(code, 0)
        self.assertEqual(doc["summary"]["local_integrity"], "failed")  # nothing is installed yet
        self.assertGreater(doc["summary"]["errors"], 0)
        vcode, vdoc = self.verify()
        self.assertEqual(vcode, 1)
        self.assertEqual(doc["summary"]["local_integrity"], vdoc["summary"]["local_integrity"])
        self.assertEqual(doc["findings"], vdoc["findings"])

    def test_status_still_exits_two_when_the_tool_cannot_run(self):
        code, out, err = run_tooling(["status", "--root", os.path.join(self.home, "nope")], self.home)
        self.assertEqual(code, 2)
        self.assertIn("ERROR USAGE", out)


class ReadOnlyTests(ToolingCase):
    def test_verify_and_status_change_nothing_and_use_no_network(self):
        self.publish_v1()
        self.apply()
        self.consumer.write("scripts/alpha.py", "edited\n")
        before = self.digest(self.consumer.root, self.upstream.root, self.home)
        for command in ("verify", "status"):
            _, doc = self.run_json(command)
            self.assertEqual(doc["network"], {"allowed": False, "used": False})
        after = self.digest(self.consumer.root, self.upstream.root, self.home)
        self.assertEqual(before, after)

    def test_verify_and_status_cannot_be_given_network_permission(self):
        self.publish_v1()
        for command in ("verify", "status"):
            code, out, err = run_tooling(self.args(command, "--allow-network"), self.home)
            self.assertEqual(code, 2)
            self.assertIn("unrecognized arguments", err)

    def test_missing_pin_object_is_reported_and_never_fetched_even_when_a_remote_could_serve_it(self):
        self.publish_v1()
        commit2 = self.publish_v2()
        # an object store that lacks the pin: a fresh empty repository
        empty = os.path.join(self.home, "empty-source")
        subprocess.run(["git", "init", "-q", empty], env=clean_env(), check=True)
        self.consumer.write_lock(commit2, repository=self.upstream.url)
        for command in ("verify", "status"):
            _, doc = self.run_json(command, source=empty)
            self.assertIn("PIN_OBJECT_MISSING", self.codes(doc, "error"))
            self.assertFalse(doc["network"]["used"])
        probe = subprocess.run(["git", "-C", empty, "cat-file", "-e", commit2], env=clean_env(),
                               capture_output=True)
        self.assertNotEqual(probe.returncode, 0, "the object appeared: something fetched")

    def test_no_bytecode_is_written(self):
        # run a pristine copy of the tool so nothing but this run can have created a .pyc
        copy = os.path.join(self.home, "tool-copy")
        shutil.copytree(str(REPO_ROOT / "scripts"), copy, ignore=shutil.ignore_patterns("__pycache__"))
        self.publish_v1()
        env = clean_env({"HOME": self.home})
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        for command in ("verify", "status"):
            subprocess.run([sys.executable, os.path.join(copy, "tooling.py"), command, "--root",
                            self.consumer.root, "--source", self.upstream.root], env=env,
                           capture_output=True, check=False)
        leftovers = [os.path.join(d, n) for d, _, names in os.walk(copy) for n in names if n.endswith(".pyc")]
        self.assertEqual(leftovers, [])


class ProvenanceWithoutTheSourceTests(ToolingCase):
    def test_without_a_source_local_integrity_is_judged_but_provenance_is_never_a_pass(self):
        self.publish_v1()
        self.apply()
        missing = os.path.join(self.home, "no-such-source")
        code, doc = self.verify(source=missing)
        self.assertEqual(code, 1)
        self.assertEqual(doc["summary"]["upstream_provenance"], "unknown")
        self.assertIn("SOURCE_UNAVAILABLE", self.codes(doc, "error"))
        self.assertEqual(sorted((r["path"], r["local"]["status"]) for r in doc["files"]),
                         [("schemas/beta.schema.json", "ok"), ("scripts/alpha.py", "ok"), ("scripts/run.sh", "ok")])
        self.assertFalse(os.path.exists(missing), "verify created the source")

    def test_the_default_source_is_read_but_never_created_by_verify(self):
        self.publish_v1()
        code, out, err = run_tooling(["verify", "--root", self.consumer.root, "--format", "json"], self.home)
        doc = json.loads(out)
        self.assertEqual(code, 1)
        self.assertIn("SOURCE_UNAVAILABLE", self.codes(doc, "error"))
        self.assertEqual(os.listdir(self.home), [])


class LockAndReceiptInputTests(ToolingCase):
    def check_lock(self, content, code_expected="LOCK_MALFORMED"):
        with open(self.consumer.lock_path, "w", encoding="utf-8") as handle:
            handle.write(content)
        code, doc = self.verify()
        self.assertEqual(code, 1, content)
        self.assertEqual(doc["summary"]["local_integrity"], "unknown")
        self.assertEqual(doc["summary"]["upstream_provenance"], "unknown")
        self.assertIn(code_expected, self.codes(doc, "error"), content)
        self.assertIsNone(doc["pin"])

    def test_a_missing_lock_is_a_finding_not_a_crash(self):
        self.publish_v1()
        os.unlink(self.consumer.lock_path)
        code, doc = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("LOCK_MISSING", self.codes(doc, "error"))

    def test_malformed_locks_are_rejected_with_a_finding(self):
        commit = self.publish_v1()
        good = {"repository": "https://example.invalid/x.git", "commit": commit}
        cases = [
            "not json", "[]", "{}",
            json.dumps(dict(good, commit="main")),
            json.dumps(dict(good, commit=commit[:12])),
            json.dumps(dict(good, commit=commit.upper())),
            json.dumps(dict(good, extra=1)),
            json.dumps(dict(good, path="scripts/alpha.py")),
            json.dumps(dict(good, path="scripts/alpha.py", sha256="zz")),
            json.dumps(dict(good, path="../escape", sha256="0" * 64)),
            json.dumps(dict(good, files="x")),
            json.dumps(dict(good, files={})),
            json.dumps(dict(good, files=None)),
            json.dumps(dict(good, files=[{"path": "scripts/a.py"}])),
            json.dumps(dict(good, files=[{"path": "scripts/a.py", "sha256": "0" * 64},
                                         {"path": "scripts/a.py", "sha256": "1" * 64}])),
            json.dumps(dict(good, files=[{"path": "scripts/a.py", "sha256": "0" * 64},
                                         {"path": "scripts/A.py", "sha256": "1" * 64}])),
            '{"repository": "r", "repository": "s", "commit": "%s"}' % commit,
        ]
        for text in cases:
            with self.subTest(text[:60]):
                self.check_lock(text)

    def test_a_lock_that_is_a_symlink_is_not_followed(self):
        commit = self.publish_v1()
        os.unlink(self.consumer.lock_path)
        real = os.path.join(self.home, "real.lock")
        with open(real, "w") as handle:
            json.dump({"repository": "r", "commit": commit}, handle)
        os.symlink(real, self.consumer.lock_path)
        code, doc = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("LOCK_MALFORMED", self.codes(doc, "error"))

    def test_a_malformed_receipt_is_reported_and_materialize_never_replaces_it(self):
        self.publish_v1()
        for broken in (b"garbage", b"[]", b'{"schema_version": 1}'):
            with self.subTest(broken):
                with open(self.consumer.receipt_path, "wb") as handle:
                    handle.write(broken)
                code, doc = self.verify()
                self.assertEqual(code, 1)
                self.assertIn("RECEIPT_MALFORMED", self.codes(doc, "error"))
                code, doc = self.materialize("--apply")
                self.assertEqual(code, 1)
                self.assertEqual(self.consumer.read(".tooltempest.receipt.json"), broken)
                self.assertFalse(self.consumer.exists("scripts/alpha.py"))


class VerifiedObjectTests(ToolingCase):
    """Bytes come from hash-verified Git objects at the pinned commit, never from a checkout."""

    def corrupt_loose(self, repo_root, oid, replacement):
        path = os.path.join(repo_root, ".git", "objects", oid[:2], oid[2:])
        os.chmod(path, 0o644)
        with open(path, "wb") as handle:
            handle.write(zlib.compress(replacement))

    def blob_oid(self, path):
        return self.upstream.fx.git("rev-parse", "%s:%s" % (self.commit1, path))

    def test_a_tampered_blob_is_detected_by_rehashing_and_nothing_is_installed(self):
        self.publish_v1()
        evil = b"print('evil')\n"
        self.corrupt_loose(self.upstream.root, self.blob_oid("scripts/alpha.py"),
                           b"blob %d\0" % len(evil) + evil)
        # git itself would hand the bytes over; the tool refuses them
        shown = subprocess.run(["git", "-C", self.upstream.root, "cat-file", "blob", self.blob_oid("scripts/alpha.py")],
                               capture_output=True, env=clean_env())
        self.assertEqual(shown.stdout, evil)
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 1)
        self.assertIn("OBJECT_CORRUPT", self.codes(doc, "error"))
        self.assertFalse(self.consumer.exists("scripts/alpha.py"))
        self.assertFalse(self.consumer.exists(".tooltempest.receipt.json"))

    def test_a_tampered_commit_or_tree_object_is_detected(self):
        self.publish_v1()
        tree = self.upstream.fx.git("rev-parse", self.commit1 + "^{tree}")
        self.corrupt_loose(self.upstream.root, tree, b"tree 0\0")
        code, doc = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("OBJECT_CORRUPT", self.codes(doc, "error"))
        self.assertEqual(doc["summary"]["upstream_provenance"], "unknown")

    def test_a_dirty_or_moved_source_checkout_never_supplies_the_bytes(self):
        self.publish_v1()
        later = self.publish_v2()
        clone = os.path.join(self.home, "cache")
        subprocess.run(["git", "clone", "-q", "--no-local", self.upstream.root, clone],
                       env=clean_env(), check=True, capture_output=True)
        subprocess.run(["git", "-C", clone, "checkout", "-q", "--detach", self.commit1],
                       env=clean_env(), check=True)
        self.consumer.write_lock(later, repository=self.upstream.url)
        # HEAD is a different commit, a tracked file is edited, another is deleted
        with open(os.path.join(clone, "scripts/alpha.py"), "w") as handle:
            handle.write("DIRTY WORKING TREE\n")
        os.unlink(os.path.join(clone, "scripts/run.sh"))
        subprocess.run(["git", "-C", clone, "config", "remote.origin.url", "https://elsewhere.invalid/x.git"],
                       env=clean_env(), check=True)
        code, doc = self.materialize("--apply", source=clone)
        self.assertEqual(code, 0, doc["findings"])
        self.assertEqual(self.consumer.read("scripts/alpha.py"), b"print('alpha v2')\n")
        self.assertEqual(self.consumer.read("scripts/run.sh"), self.upstream.files["scripts/run.sh"])
        self.assertIn("SOURCE_REMOTE_MISMATCH", self.codes(doc, "warning"))
        self.assertIs(doc["source"]["remote_matches_lock"], False)
        code, doc = self.verify(source=clone)
        self.assertEqual(code, 0)
        with open(os.path.join(clone, "scripts/alpha.py")) as handle:
            self.assertEqual(handle.read(), "DIRTY WORKING TREE\n")  # the cache was not repaired either

    def test_a_matching_remote_is_noted_as_a_fact_not_a_trust_decision(self):
        self.publish_v1()
        self.upstream.set_file_url(UNREACHABLE_URL)
        self.consumer.write_lock(self.commit1, repository=UNREACHABLE_URL)
        _, doc = self.verify()
        self.assertIs(doc["source"]["remote_matches_lock"], True)


class Sha256Tests(ToolingCase):
    def test_sha256_object_format_repositories_are_supported(self):
        self.upstream.cleanup()
        self.consumer.cleanup()
        self.upstream = Upstream("sha256")
        self.consumer = Consumer("sha256")
        self.addCleanup(self.upstream.cleanup)
        self.addCleanup(self.consumer.cleanup)
        commit = self.publish_v1()
        self.assertEqual(len(commit), 64)
        self.apply()
        code, doc = self.verify()
        self.assertEqual(code, 0)
        self.assertEqual(len(doc["pin"]["commit"]), 64)
        self.assertEqual(len(self.file(doc, "scripts/alpha.py")["pinned"]["git_blob"]), 64)
        self.assertReceiptValid()


class ToolFailureTests(ToolingCase):
    def test_usage_and_environment_errors_are_exit_two_with_an_error_object(self):
        self.publish_v1()
        code, out, _ = run_tooling(["verify", "--root", os.path.join(self.home, "missing"), "--format", "json"], self.home)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["code"], "USAGE")
        code, out, _ = run_tooling(["verify", "--format", "json"], self.home, cwd=self.home)
        self.assertEqual(code, 2)  # not inside a Git checkout, no --root
        self.assertEqual(json.loads(out)["error"]["code"], "USAGE")
        for argv in ([], ["frobnicate"], ["verify", "--nonsense"]):
            code, out, err = run_tooling(argv, self.home)
            self.assertEqual((code, out), (2, ""), argv)

    def test_a_missing_git_binary_is_a_tool_failure_not_a_missing_source(self):
        self.publish_v1()
        env = clean_env({"HOME": self.home, "PATH": os.path.join(self.home, "no-bin")})
        code, out, err = run_tooling(self.args("verify"), self.home, env=env)
        doc = json.loads(out)
        self.assertEqual(code, 2)
        self.assertEqual(doc["error"]["code"], "GIT_MISSING")

    def test_a_missing_git_binary_is_found_out_when_the_root_is_looked_up_too(self):
        self.publish_v1()
        env = clean_env({"HOME": self.home, "PATH": os.path.join(self.home, "no-bin")})
        code, out, err = run_tooling(["verify", "--source", self.upstream.root, "--format", "json"], self.home,
                                     cwd=self.consumer.root, env=env)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["code"], "GIT_MISSING")

    def test_a_broken_installation_of_the_tool_still_exits_two(self):
        lone = os.path.join(self.home, "lone")
        os.mkdir(lone)
        shutil.copy(str(TOOLING_PY), lone)
        proc = subprocess.run([sys.executable, "-I", os.path.join(lone, "tooling.py"), "verify"],
                              capture_output=True, env=clean_env({"HOME": self.home}))
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)["error"]["code"], "IMPORT_FAILED")

    def test_text_output_for_a_tool_failure_says_nothing_was_verified(self):
        code, out, err = run_tooling(["verify", "--root", os.path.join(self.home, "nope")], self.home)
        self.assertEqual(code, 2)
        self.assertIn("nothing was verified or changed", out)


class EnvironmentAndInputEdgeTests(ToolingCase):
    def test_a_hooks_git_environment_cannot_redirect_the_tool(self):
        # git sets GIT_DIR / GIT_INDEX_FILE inside hooks; neither may decide what is read
        self.publish_v1()
        self.apply()
        env = clean_env({"HOME": self.home, "GIT_DIR": os.path.join(self.home, "elsewhere"),
                         "GIT_INDEX_FILE": os.path.join(self.home, "nope-index"),
                         "GIT_WORK_TREE": self.home})
        code, out, err = run_tooling(self.args("verify"), self.home, env=env)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(json.loads(out)["summary"]["local_integrity"], "ok")

    def test_a_source_that_is_a_file_or_not_a_repository_is_an_unavailable_source(self):
        self.publish_v1()
        plain_file = os.path.join(self.home, "afile")
        with open(plain_file, "w") as handle:
            handle.write("x")
        plain_dir = os.path.join(self.home, "adir")
        os.mkdir(plain_dir)
        for source in (plain_file, plain_dir):
            with self.subTest(source):
                code, doc = self.verify(source=source)
                self.assertEqual(code, 1)
                self.assertIn("SOURCE_UNAVAILABLE", self.codes(doc, "error"))

    def test_a_linked_worktree_of_the_upstream_can_serve_as_the_source(self):
        self.publish_v1()
        worktree = os.path.join(self.home, "upstream-wt")
        self.upstream.fx.git("worktree", "add", "-q", "--detach", worktree, self.commit1)
        code, doc = self.materialize("--apply", source=worktree)
        self.assertEqual(code, 0, doc["findings"])
        self.assertEqual(self.verify(source=worktree)[0], 0)

    def test_a_blob_over_the_size_limit_is_refused_not_truncated(self):
        from unittest import mock
        from tooling_core import objects
        self.publish_v1()
        with mock.patch.object(objects, "MAX_BLOB_BYTES", 10):
            doc = engine.run_materialize(self.engine_options(apply=True))
        self.assertEqual(doc["summary"]["exit_code"], 1)
        self.assertIn("OBJECT_CORRUPT", self.codes(doc, "error"))
        self.assertFalse(self.consumer.exists("scripts/alpha.py"))

    def test_custom_lock_and_receipt_paths_are_honoured(self):
        commit = self.publish_v1()
        os.unlink(self.consumer.lock_path)
        lock = os.path.join(self.consumer.root, "config", "pin.json")
        receipt = os.path.join(self.consumer.root, "config", "installed.json")
        os.makedirs(os.path.dirname(lock))
        with open(lock, "w") as handle:
            json.dump({"repository": "r", "commit": commit}, handle)
        extra = ["--lock", lock, "--receipt", receipt]
        code, doc = self.materialize("--apply", *extra)
        self.assertEqual(code, 0, doc["findings"])
        self.assertTrue(os.path.isfile(receipt))
        self.assertFalse(self.consumer.exists(".tooltempest.receipt.json"))
        self.assertEqual(self.verify(*extra)[0], 0)

    def test_unicode_names_and_empty_files_install_and_verify(self):
        commit = self.upstream.publish({"scripts/\u00fcn\u00ef.py": "x = 1\n", "scripts/empty.py": ""},
                                       message="odd names")
        self.consumer.write_lock(commit)
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 0, doc["findings"])
        self.assertEqual(self.consumer.read("scripts/empty.py"), b"")
        self.assertEqual(self.consumer.read("scripts/\u00fcn\u00ef.py"), b"x = 1\n")
        self.assertEqual(self.verify()[0], 0)

    def test_a_tampered_receipt_hash_is_a_provenance_failure_and_blocks_materialize(self):
        self.publish_v1()
        self.apply()
        receipt = self.consumer.receipt()
        receipt["files"][0]["sha256"] = "0" * 64
        with open(self.consumer.receipt_path, "w") as handle:
            json.dump(receipt, handle)
        code, doc = self.verify()
        self.assertEqual(code, 1)
        self.assertEqual(doc["summary"]["upstream_provenance"], "failed")
        self.assertIn("PROVENANCE_MISMATCH", self.codes(doc, "error"))
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 1)
        self.assertEqual(self.consumer.receipt()["files"][0]["sha256"], "0" * 64)


class PinIdentityTests(ToolingCase):
    def test_a_pin_that_names_a_blob_or_a_tree_is_not_a_commit(self):
        self.publish_v1()
        blob = self.upstream.fx.git("rev-parse", "%s:scripts/alpha.py" % self.commit1)
        tree = self.upstream.fx.git("rev-parse", "%s^{tree}" % self.commit1)
        for oid in (blob, tree):
            with self.subTest(oid):
                self.consumer.write_lock(oid)
                code, doc = self.materialize("--apply")
                self.assertEqual(code, 1)
                self.assertIn("OBJECT_CORRUPT", self.codes(doc, "error"))
                self.assertFalse(self.consumer.exists("scripts/alpha.py"))

    def test_a_commit_object_without_a_tree_line_is_corrupt(self):
        self.publish_v1()
        odd = self.upstream.fx.git("hash-object", "-t", "commit", "-w", "--literally", "--stdin",
                                   input_bytes=b"author nobody\n\nno tree here\n")
        self.consumer.write_lock(odd)
        code, doc = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("OBJECT_CORRUPT", self.codes(doc, "error"))
        self.assertIn("tree", " ".join(f["message"] for f in doc["findings"]))

    def test_a_commit_naming_a_tree_of_the_wrong_hash_length_is_corrupt(self):
        self.publish_v1()
        odd = self.upstream.fx.git("hash-object", "-t", "commit", "-w", "--literally", "--stdin",
                                   input_bytes=("tree %s\n\nwrong width\n" % ("a" * 64)).encode())
        self.consumer.write_lock(odd)
        code, doc = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("OBJECT_CORRUPT", self.codes(doc, "error"))

    def test_a_manifest_path_beneath_a_file_at_the_pin_is_simply_absent(self):
        commit = self.upstream.publish({"scripts/f.py": "f\n"}, manifest="scripts/f.py/x\n", message="odd")
        self.consumer.write_lock(commit)
        code, doc = self.verify()
        self.assertEqual(code, 1)
        self.assertEqual(self.codes(doc, "error").count("SOURCE_FILE_MISSING"), 1)
        self.assertNotIn("OBJECT_CORRUPT", self.codes(doc))

    def test_remote_urls_match_regardless_of_a_dot_git_suffix_or_trailing_slash(self):
        from tooling_core import engine
        self.assertTrue(engine._same_remote("https://h/x/y.git", "https://h/x/y"))
        self.assertTrue(engine._same_remote("https://h/x/y/", "https://h/x/y.git"))
        self.assertFalse(engine._same_remote("https://h/x/y.git", "https://h/x/z.git"))


class VerifyExitTests(ToolingCase):
    def test_a_structural_error_fails_verify_even_when_every_installed_file_is_fine(self):
        commit = self.publish_v1()
        self.apply()
        digest = sha256(self.upstream.files["scripts/alpha.py"])
        self.consumer.write_lock(commit, declared={"scripts/alpha.py": digest, "scripts/ghost.py": digest})
        code, doc = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("DECLARED_PATH_NOT_INSTALLABLE", self.codes(doc, "error"))
        self.assertEqual((doc["summary"]["local_integrity"], doc["summary"]["upstream_provenance"]), ("ok", "ok"))

    def test_one_diagnosis_per_cause_not_a_cascade(self):
        self.publish_v1()
        tree = self.upstream.fx.git("rev-parse", self.commit1 + "^{tree}")
        path = os.path.join(self.upstream.root, ".git", "objects", tree[:2], tree[2:])
        os.chmod(path, 0o644)
        with open(path, "wb") as handle:
            handle.write(zlib.compress(b"tree 0\0"))
        code, doc = self.verify()
        self.assertIn("OBJECT_CORRUPT", self.codes(doc, "error"))
        self.assertNotIn("PIN_OBJECT_MISSING", self.codes(doc))

    def test_the_default_source_is_the_shared_cache_under_home(self):
        self.publish_v1()
        self.apply()
        cache = os.path.join(self.home, ".cache", "tooltempest", "repo")
        os.makedirs(os.path.dirname(cache))
        subprocess.run(["git", "clone", "-q", "--no-local", self.upstream.root, cache], env=clean_env(),
                       check=True, capture_output=True)
        code, out, err = run_tooling(["verify", "--root", self.consumer.root, "--format", "json"], self.home)
        doc = json.loads(out)
        self.assertEqual(code, 0, out)
        self.assertEqual(os.path.realpath(doc["source"]["path"]), os.path.realpath(cache))

    def test_an_unexpected_internal_failure_is_exit_two_never_a_pass(self):
        from unittest import mock
        from tooling_core import cli
        self.publish_v1()
        with mock.patch.object(cli, "evaluate", side_effect=RuntimeError("bug")):
            with mock.patch.object(sys, "stdout", new=__import__("io").StringIO()) as out:
                code = cli.main(["verify", "--root", self.consumer.root, "--source", self.upstream.root,
                                 "--format", "json"])
        doc = json.loads(out.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(doc["error"]["code"], "INTERNAL_ERROR")
        self.assertIn("bug", doc["error"]["message"])

    def test_the_text_report_names_each_property_and_the_receipt(self):
        self.publish_v1()
        self.apply()
        self.publish_v2()
        code, out, err = run_tooling(["verify", "--root", self.consumer.root, "--source", self.upstream.root], self.home)
        self.assertEqual(code, 0)
        for line in ("local integrity      ok", "upstream provenance  ok", "freshness            behind",
                     "1 commit(s) behind refs/", "receipt              complete, commit", "network              not used",
                     "[warning] PIN_BEHIND", "exit 0"):
            self.assertIn(line, out)
        self.assertNotIn("(not the pin)", out)
        self.consumer.write_lock(self.commit2)
        code, out, err = run_tooling(["verify", "--root", self.consumer.root, "--source", self.upstream.root], self.home)
        self.assertEqual(code, 1)
        self.assertIn("(not the pin)", out)
        self.assertIn("outdated", out)


class SourceShapeTests(ToolingCase):
    def test_a_plain_subdirectory_of_another_repository_is_not_a_source(self):
        self.publish_v1()
        inside = os.path.join(self.upstream.root, "scripts")
        code, doc = self.verify(source=inside)
        self.assertEqual(code, 1)
        self.assertIn("SOURCE_UNAVAILABLE", self.codes(doc, "error"))
        self.assertNotIn("PIN_OBJECT_MISSING", self.codes(doc))

    def test_a_bare_repository_and_a_git_directory_can_be_sources(self):
        self.publish_v1()
        bare = os.path.join(self.home, "bare.git")
        subprocess.run(["git", "clone", "-q", "--bare", "--no-local", self.upstream.root, bare],
                       env=clean_env(), check=True, capture_output=True)
        self.assertEqual(self.verify(source=bare)[1]["source"]["available"], True)
        code, doc = self.materialize("--apply", source=bare)
        self.assertEqual(code, 0, doc["findings"])
        self.assertEqual(self.verify(source=os.path.join(self.upstream.root, ".git"))[0], 0)


class CraftedObjectGraphTests(ToolingCase):
    """Objects a healthy Git never writes: entries whose mode and object type disagree."""

    def hash_object(self, kind, content=b""):
        return self.upstream.fx.git("hash-object", "-t", kind, "-w", "--literally", "--stdin", input_bytes=content)

    @staticmethod
    def tree(*entries):
        return b"".join(mode.encode() + b" " + name.encode() + b"\0" + bytes.fromhex(oid)
                        for mode, name, oid in entries)

    def craft_commit(self, root_entries):
        root = self.hash_object("tree", self.tree(*root_entries))
        commit = self.hash_object("commit", ("tree %s\n\ncrafted\n" % root).encode())
        self.consumer.write_lock(commit)
        return commit

    def test_a_file_entry_that_points_at_a_tree_object_is_not_file_content(self):
        manifest = self.hash_object("blob", b"scripts/x.py\n")
        empty_tree = self.hash_object("tree", b"")
        scripts = self.hash_object("tree", self.tree(("100644", "x.py", empty_tree)))
        self.craft_commit([("100644", "MANIFEST.txt", manifest), ("40000", "scripts", scripts)])
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 1)
        self.assertIn("OBJECT_CORRUPT", self.codes(doc, "error"))
        self.assertIn("expected a blob", " ".join(f["message"] for f in doc["findings"]))
        self.assertFalse(self.consumer.exists("scripts/x.py"), "a tree object was installed as an empty file")

    def test_a_directory_entry_that_points_at_a_blob_is_corrupt(self):
        manifest = self.hash_object("blob", b"scripts/x.py\n")
        not_a_tree = self.hash_object("blob", b"just bytes")
        self.craft_commit([("100644", "MANIFEST.txt", manifest), ("40000", "scripts", not_a_tree)])
        code, doc = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("OBJECT_CORRUPT", self.codes(doc, "error"))


class CommitCorruptionAndEmptySetTests(ToolingCase):
    def test_a_tampered_commit_object_is_one_diagnosis_not_two(self):
        self.publish_v1()
        path = os.path.join(self.upstream.root, ".git", "objects", self.commit1[:2], self.commit1[2:])
        os.chmod(path, 0o644)
        with open(path, "wb") as handle:
            handle.write(zlib.compress(b"commit 12\0tree nothing"))
        code, doc = self.verify()
        self.assertEqual(code, 1)
        self.assertEqual(self.codes(doc, "error"), ["OBJECT_CORRUPT"])

    def test_an_installable_set_that_is_empty_cannot_be_called_established_when_something_is_wrong(self):
        commit = self.upstream.publish({"skills/k/SKILL.md": "k\n"}, message="only global files")
        self.consumer.write_lock(commit, declared={"scripts/ghost.py": "0" * 64})
        code, doc = self.verify()
        self.assertEqual(code, 1)
        self.assertEqual(doc["files"], [])
        self.assertEqual((doc["summary"]["local_integrity"], doc["summary"]["upstream_provenance"]),
                         ("unknown", "unknown"))

    def test_nothing_to_install_and_nothing_wrong_is_established(self):
        commit = self.upstream.publish({"skills/k/SKILL.md": "k\n"}, message="only global files")
        self.consumer.write_lock(commit)
        code, doc = self.verify()
        self.assertEqual(code, 0)
        self.assertEqual((doc["summary"]["local_integrity"], doc["summary"]["upstream_provenance"]), ("ok", "ok"))


class GitFailureContractTests(ToolingCase):
    def test_a_failing_git_read_is_a_tool_failure_exit_two_not_a_finding(self):
        # ADR-0015 Decision 10: bad inputs are findings; Git itself failing is exit 2
        import io
        from unittest import mock
        from tooling_core import cli
        from tooling_core.objects import GitFailure, ObjectStore
        self.publish_v1()
        with mock.patch.object(ObjectStore, "read_object", side_effect=GitFailure("cat-file died")):
            with mock.patch.object(sys, "stdout", new=io.StringIO()) as out:
                code = cli.main(["verify", "--root", self.consumer.root, "--source", self.upstream.root,
                                 "--format", "json"])
        doc = json.loads(out.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(doc["error"]["code"], "GIT_FAILED")
        self.assertIn("cat-file died", doc["error"]["message"])
