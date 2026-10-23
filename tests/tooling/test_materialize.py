"""materialize: dry run by default, receipts, upgrades, conflicts, modes, scope.

Proven here: nothing is written without --apply; installed bytes equal the
verified blobs at the pinned commit and are re-verified afterwards; a
known-managed earlier version is upgraded, while an edited managed file and
an unmanaged conflict are each refused with their bytes and mode preserved
and nothing else written; repeated runs change nothing; modes (including
the executable bit) survive any umask; only scripts/ and schemas/ are
installed and nothing under HOME is touched.
"""
import json
import os

from tooling_helpers import UNREACHABLE_URL, ToolingCase, run_tooling, sha256


class DryRunTests(ToolingCase):
    def test_materialize_writes_nothing_without_apply(self):
        self.publish_v1()
        before = self.digest(self.consumer.root, self.upstream.root, self.home)
        code, doc = self.materialize()
        self.assertEqual(code, 0)
        self.assertTrue(doc["result"]["dry_run"])
        self.assertFalse(doc["result"]["applied"])
        self.assertEqual(sorted(r["action"] for r in doc["files"]), ["create"] * 3)
        self.assertEqual(before, self.digest(self.consumer.root, self.upstream.root, self.home))
        self.assertFalse(self.consumer.exists(".tooltempest.receipt.json"))
        git_dir = self.consumer.fx.git("rev-parse", "--absolute-git-dir")
        self.assertFalse(os.path.exists(os.path.join(git_dir, "tooltempest-install.lock")),
                         "a dry run created a lock file")

    def test_the_dry_run_text_says_that_nothing_was_written(self):
        self.publish_v1()
        code, out, _ = run_tooling(["materialize", "--root", self.consumer.root, "--source",
                                    self.upstream.root], self.home)
        self.assertEqual(code, 0)
        self.assertIn("dry run: no file and no receipt was written", out)
        self.assertIn("--apply", out)


class InstallTests(ToolingCase):
    def test_apply_installs_the_pinned_bytes_and_writes_a_receipt(self):
        commit = self.publish_v1()
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 0)
        self.assertTrue(doc["result"]["applied"])
        self.assertEqual(sorted(doc["result"]["written"]),
                         ["schemas/beta.schema.json", "scripts/alpha.py", "scripts/run.sh"])
        self.assertEqual(doc["result"]["post_verification"],
                         {"local_integrity": "ok", "upstream_provenance": "ok", "ok": True})
        for path in ("scripts/alpha.py", "scripts/run.sh", "schemas/beta.schema.json"):
            self.assertEqual(self.consumer.read(path), self.upstream.files[path], path)
        receipt = self.consumer.receipt()
        self.assertEqual((receipt["state"], receipt["commit"], receipt["repository"]),
                         ("complete", commit, UNREACHABLE_URL))
        by_path = {f["path"]: f for f in receipt["files"]}
        self.assertEqual(sorted(by_path), ["schemas/beta.schema.json", "scripts/alpha.py", "scripts/run.sh"])
        self.assertEqual(by_path["scripts/alpha.py"]["sha256"], sha256(self.upstream.files["scripts/alpha.py"]))
        self.assertEqual(by_path["scripts/alpha.py"]["git_blob"],
                         self.upstream.fx.git("rev-parse", "%s:scripts/alpha.py" % commit))
        self.assertEqual((by_path["scripts/run.sh"]["mode"], by_path["scripts/alpha.py"]["mode"]),
                         ("100755", "100644"))
        self.assertReceiptValid()

    def test_modes_survive_any_umask_and_the_executable_bit_is_kept(self):
        self.publish_v1()
        code, doc = self.materialize("--apply", umask=0o077)
        self.assertEqual(code, 0)
        self.assertEqual(self.consumer.mode("scripts/run.sh"), 0o755)
        self.assertEqual(self.consumer.mode("scripts/alpha.py"), 0o644)
        self.assertEqual(self.verify()[0], 0)

    def test_a_mode_change_upstream_is_applied_on_upgrade(self):
        self.publish_v1()
        self.apply()
        self.commit2 = self.upstream.publish({"scripts/run.sh": "#!/bin/sh\necho v2\n"},
                                             unexecutable=("scripts/run.sh",), message="drop +x")
        self.consumer.write_lock(self.commit2)
        self.apply()
        self.assertEqual(self.consumer.mode("scripts/run.sh"), 0o644)
        self.assertEqual({f["path"]: f["mode"] for f in self.consumer.receipt()["files"]}["scripts/run.sh"], "100644")

    def test_only_scripts_and_schemas_are_installed_and_global_files_are_never_touched(self):
        self.publish_v1()
        skill = os.path.join(self.home, ".claude", "skills", "demo", "SKILL.md")
        os.makedirs(os.path.dirname(skill))
        with open(skill, "w") as handle:
            handle.write("# the user's own global skill\n")
        before_home = self.digest(self.home)
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 0)
        self.assertEqual(self.digest(self.home), before_home)
        self.assertFalse(self.consumer.exists("skills"))
        self.assertFalse(self.consumer.exists("docs"))
        skipped = {item["path"]: item["reason"] for item in doc["skipped"]}
        self.assertEqual(sorted(skipped), ["docs/reference/rules.md", "skills/demo/SKILL.md"])
        self.assertIn("~/.claude", skipped["skills/demo/SKILL.md"])
        self.assertIn("reference", skipped["docs/reference/rules.md"])
        self.assertEqual([r["path"] for r in doc["files"] if r["path"].startswith(("skills", "docs"))], [])


class RepeatedRunTests(ToolingCase):
    def test_a_second_apply_changes_nothing_at_all(self):
        self.publish_v1()
        self.apply()
        for rel in ("scripts/alpha.py", ".tooltempest.receipt.json"):
            os.utime(self.consumer.path(rel), ns=(10 ** 9, 10 ** 9))
        before = self.digest(self.consumer.root)
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 0)
        self.assertEqual(doc["result"]["written"], [])
        self.assertFalse(doc["result"]["receipt_written"])
        self.assertEqual({r["action"] for r in doc["files"]}, {"current"})
        self.assertEqual(self.digest(self.consumer.root), before)  # bytes and mtimes

    def test_applying_after_a_manual_hand_install_adopts_without_rewriting_a_byte(self):
        # files installed by an older script (no receipt) and identical to the pin are recorded, not rewritten
        self.publish_v1()
        for path in ("scripts/alpha.py", "scripts/run.sh", "schemas/beta.schema.json"):
            self.consumer.write(path, self.upstream.files[path],
                                mode=0o755 if path.endswith(".sh") else 0o644)
            os.utime(self.consumer.path(path), ns=(10 ** 9, 10 ** 9))
        code, doc = self.verify()
        self.assertEqual(code, 1)  # present and equal to the pin, yet no declaration says so
        self.assertEqual(doc["summary"]["local_integrity"], "unknown")
        self.assertEqual({r["matches_pin"] for r in doc["files"]}, {True})
        self.assertIn("UNRECORDED_FILE", self.codes(doc, "error"))
        self.assertIn("PROVENANCE_UNESTABLISHED", self.codes(doc, "error"))
        self.assertEqual(doc["summary"]["upstream_provenance"], "unknown")
        code, dry = self.materialize()
        self.assertEqual({r["action"] for r in dry["files"]}, {"record"})
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 0)
        self.assertEqual(doc["result"]["written"], [])
        self.assertTrue(doc["result"]["receipt_written"])
        for path in ("scripts/alpha.py", "scripts/run.sh", "schemas/beta.schema.json"):
            self.assertEqual(os.lstat(self.consumer.path(path)).st_mtime_ns, 10 ** 9, path)
        self.assertEqual(self.verify()[0], 0)

    def test_a_declared_hash_in_the_lock_is_checked_against_the_blob_and_then_installs(self):
        commit = self.publish_v1()
        digest = sha256(self.upstream.files["scripts/alpha.py"])
        self.consumer.write_lock(commit, declared={"scripts/alpha.py": digest})
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 0)
        record = self.file(doc, "scripts/alpha.py")
        self.assertEqual((record["declared"], record["provenance"]), ({"sha256": digest, "by": "lock"}, "verified"))
        self.assertEqual(self.file(self.verify()[1], "scripts/run.sh")["declared"]["by"], "receipt")

    def test_the_legacy_single_file_lock_declares_that_files_hash(self):
        commit = self.publish_v1()
        digest = sha256(self.upstream.files["scripts/alpha.py"])
        self.consumer.write_lock(commit, legacy=("scripts/alpha.py", digest))
        self.assertEqual(self.materialize("--apply")[0], 0)
        _, doc = self.verify()
        self.assertEqual(self.file(doc, "scripts/alpha.py")["declared"]["by"], "lock")

    def test_a_wrong_declared_hash_refuses_everything_and_writes_nothing(self):
        commit = self.publish_v1()
        self.consumer.write_lock(commit, declared={"scripts/alpha.py": "0" * 64})
        before = self.digest(self.consumer.root)
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 1)
        self.assertIn("PROVENANCE_MISMATCH", self.codes(doc, "error"))
        self.assertFalse(doc["result"]["applied"])
        self.assertEqual([r["action"] for r in doc["files"]], [None] * 3, "a plan was made despite the error")
        self.assertEqual(self.digest(self.consumer.root), before)

    def test_a_lock_declaring_a_path_the_manifest_does_not_install_is_refused(self):
        commit = self.publish_v1()
        self.consumer.write_lock(commit, declared={"scripts/ghost.py": "0" * 64})
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 1)
        self.assertIn("DECLARED_PATH_NOT_INSTALLABLE", self.codes(doc, "error"))
        self.assertFalse(self.consumer.exists("scripts/alpha.py"))


class UpgradeTests(ToolingCase):
    def install_v1_then_pin_v2(self):
        self.publish_v1()
        self.apply()
        self.publish_v2()
        self.consumer.write_lock(self.commit2)

    def test_a_known_managed_earlier_version_is_upgraded_to_the_new_pin(self):
        self.install_v1_then_pin_v2()
        code, doc = self.verify()
        self.assertEqual(code, 1)  # the pin moved; the installation is behind it
        self.assertEqual(self.file(doc, "scripts/alpha.py")["local"]["status"], "outdated")
        for unchanged in ("scripts/run.sh", "schemas/beta.schema.json"):  # same bytes at both commits
            self.assertEqual(self.file(doc, unchanged)["local"]["status"], "ok", unchanged)
            self.assertEqual((self.file(doc, unchanged)["declared"]["by"], self.file(doc, unchanged)["provenance"]),
                             ("receipt", "verified"))
        self.assertEqual(self.file(doc, "scripts/alpha.py")["provenance"], "undeclared")
        self.assertIn("INSTALL_BEHIND_PIN", self.codes(doc, "error"))
        # the receipt vouches for the old commit only: nothing declares the new pin's bytes yet
        self.assertEqual((doc["summary"]["local_integrity"], doc["summary"]["upstream_provenance"]),
                         ("failed", "unknown"))
        code, dry = self.materialize()
        self.assertEqual(self.file(dry, "scripts/alpha.py")["action"], "upgrade")
        self.assertEqual(self.file(dry, "scripts/alpha.py")["reason"][:20], "known-managed earlie")
        self.assertEqual(self.file(dry, "schemas/gamma.schema.json")["action"], "create")
        self.assertEqual(self.file(dry, "scripts/run.sh")["action"], "record")  # bytes fine; receipt moves to the new pin
        self.assertEqual(self.consumer.read("scripts/alpha.py"), b"print('alpha v1')\n")
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(doc["result"]["written"]), ["schemas/gamma.schema.json", "scripts/alpha.py"])
        self.assertEqual(self.consumer.read("scripts/alpha.py"), b"print('alpha v2')\n")
        self.assertEqual(self.consumer.receipt()["commit"], self.commit2)
        self.assertEqual(self.verify()[0], 0)

    def test_only_the_changed_files_are_rewritten(self):
        self.install_v1_then_pin_v2()
        for rel in ("scripts/run.sh", "schemas/beta.schema.json"):
            os.utime(self.consumer.path(rel), ns=(10 ** 9, 10 ** 9))
        self.apply()
        for rel in ("scripts/run.sh", "schemas/beta.schema.json"):
            self.assertEqual(os.lstat(self.consumer.path(rel)).st_mtime_ns, 10 ** 9, rel)

    def test_a_file_dropped_from_the_manifest_is_left_in_place_and_leaves_the_receipt(self):
        self.install_v1_then_pin_v2()
        self.commit3 = self.upstream.publish(remove=("schemas/beta.schema.json",), message="drop beta")
        self.consumer.write_lock(self.commit3)
        self.apply()
        self.assertTrue(self.consumer.exists("schemas/beta.schema.json"), "nothing is ever deleted")
        self.assertNotIn("schemas/beta.schema.json", [f["path"] for f in self.consumer.receipt()["files"]])

    def test_the_pin_is_never_changed_by_any_command(self):
        self.install_v1_then_pin_v2()
        lock_before = self.consumer.read(".tooltempest.lock")
        self.apply()
        self.verify()
        self.run_json("status")
        self.assertEqual(self.consumer.read(".tooltempest.lock"), lock_before)


class ConflictTests(ToolingCase):
    def setUp(self):
        super().setUp()
        self.publish_v1()
        self.apply()
        self.publish_v2()
        self.consumer.write_lock(self.commit2)

    def refuse_and_assert_untouched(self, expected_code, edited):
        before = self.digest(self.consumer.root)
        receipt_before = self.consumer.read(".tooltempest.receipt.json")
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 1)
        self.assertIn(expected_code, self.codes(doc, "error"))
        self.assertFalse(doc["result"]["applied"])
        self.assertEqual(doc["result"]["written"], [])
        self.assertEqual(self.digest(self.consumer.root), before,
                         "a refused run changed the checkout (all-or-nothing violated)")
        self.assertEqual(self.consumer.read(".tooltempest.receipt.json"), receipt_before)
        self.assertEqual(self.file(doc, edited)["action"], "refuse")
        self.assertFalse(self.consumer.exists("schemas/gamma.schema.json"),
                         "other files were installed although one was refused")
        return doc

    def test_a_locally_edited_managed_file_is_refused_with_bytes_and_mode_preserved(self):
        self.consumer.write("scripts/alpha.py", "print('my local edit')\n", mode=0o700)
        doc = self.refuse_and_assert_untouched("CONFLICT_MANAGED_MODIFIED", "scripts/alpha.py")
        self.assertEqual(self.consumer.read("scripts/alpha.py"), b"print('my local edit')\n")
        self.assertEqual(self.consumer.mode("scripts/alpha.py"), 0o700)
        self.assertNotIn("CONFLICT_UNMANAGED", self.codes(doc))

    def test_a_mode_only_edit_of_a_managed_file_is_also_an_edit(self):
        os.chmod(self.consumer.path("scripts/alpha.py"), 0o755)
        self.refuse_and_assert_untouched("CONFLICT_MANAGED_MODIFIED", "scripts/alpha.py")
        self.assertEqual(self.consumer.mode("scripts/alpha.py"), 0o755)

    def test_an_unmanaged_file_in_the_way_is_a_different_refusal(self):
        os.unlink(self.consumer.path("scripts/alpha.py"))
        os.unlink(self.consumer.path(".tooltempest.receipt.json"))
        self.consumer.write("scripts/alpha.py", "something that is not ours\n", mode=0o750)
        before = self.digest(self.consumer.root)
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 1)
        self.assertIn("CONFLICT_UNMANAGED", self.codes(doc, "error"))
        self.assertNotIn("CONFLICT_MANAGED_MODIFIED", self.codes(doc))
        self.assertEqual(self.digest(self.consumer.root), before)
        self.assertEqual(self.consumer.mode("scripts/alpha.py"), 0o750)
        self.assertEqual(self.consumer.read("scripts/alpha.py"), b"something that is not ours\n")

    def test_refusals_are_visible_in_the_dry_run_too(self):
        self.consumer.write("scripts/alpha.py", "edit\n")
        before = self.digest(self.consumer.root)
        code, doc = self.materialize()
        self.assertEqual(code, 1)
        self.assertIn("CONFLICT_MANAGED_MODIFIED", self.codes(doc, "error"))
        self.assertEqual(self.digest(self.consumer.root), before)

    def test_once_the_user_restores_the_managed_bytes_the_upgrade_goes_through(self):
        self.consumer.write("scripts/alpha.py", "edit\n")
        self.assertEqual(self.materialize("--apply")[0], 1)
        # restore the v1 bytes the receipt knows
        self.consumer.write("scripts/alpha.py", "print('alpha v1')\n")
        self.assertEqual(self.materialize("--apply")[0], 0)
        self.assertEqual(self.verify()[0], 0)

    def test_an_edit_that_happens_to_equal_the_new_pin_is_simply_recorded(self):
        self.consumer.write("scripts/alpha.py", "print('alpha v2')\n")
        code, dry = self.materialize()
        self.assertEqual(code, 0)
        self.assertEqual(self.file(dry, "scripts/alpha.py")["action"], "record")
        self.assertEqual(self.materialize("--apply")[0], 0)
        self.assertEqual(self.verify()[0], 0)


class RestoreAndRenderTests(ToolingCase):
    def test_a_deleted_managed_file_is_restored_from_the_pin(self):
        self.publish_v1()
        self.apply()
        os.unlink(self.consumer.path("scripts/alpha.py"))
        code, doc = self.verify()
        self.assertEqual(code, 1)
        self.assertEqual(self.file(doc, "scripts/alpha.py")["local"]["status"], "missing")
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 0)
        self.assertEqual(doc["result"]["written"], ["scripts/alpha.py"])
        self.assertEqual(self.verify()[0], 0)

    def test_a_refusal_is_explained_in_plain_text_and_says_nothing_was_written(self):
        self.publish_v1()
        self.consumer.write("scripts/alpha.py", "mine\n")
        code, out, err = run_tooling(["materialize", "--apply", "--root", self.consumer.root, "--source",
                                      self.upstream.root], self.home)
        self.assertEqual(code, 1)
        self.assertIn("CONFLICT_UNMANAGED", out)
        self.assertIn("never overwrites unrecognised content", out)
        self.assertIn("nothing was written", out)
        self.assertIn("not applied: 0 file(s) written", out)
        self.assertEqual(err, "")


class ModeSemanticsTests(ToolingCase):
    def test_only_the_owner_execute_bit_counts_as_executable(self):
        self.publish_v1()
        self.apply()
        os.chmod(self.consumer.path("scripts/run.sh"), 0o744)  # still executable for the owner
        code, doc = self.verify()
        self.assertEqual(code, 0, doc["findings"])
        self.assertEqual(self.file(doc, "scripts/run.sh")["local"]["mode"], "100755")
        os.chmod(self.consumer.path("scripts/run.sh"), 0o655)  # group/other bits only: not executable
        code, doc = self.verify()
        self.assertEqual(code, 1)
        self.assertEqual(self.file(doc, "scripts/run.sh")["local"]["mode"], "100644")
        self.assertEqual(self.file(doc, "scripts/run.sh")["local"]["status"], "modified")
        self.assertIs(self.file(doc, "scripts/run.sh")["matches_pin"], False)

    def test_a_declared_and_recorded_file_whose_mode_changed_is_modified(self):
        commit = self.publish_v1()
        digest = sha256(self.upstream.files["scripts/run.sh"])
        self.consumer.write_lock(commit, declared={"scripts/run.sh": digest})
        self.apply()
        os.chmod(self.consumer.path("scripts/run.sh"), 0o644)
        code, doc = self.verify()
        self.assertEqual(code, 1)
        self.assertEqual(self.file(doc, "scripts/run.sh")["local"]["status"], "modified")


class DeclaredAndReceiptEdgeTests(ToolingCase):
    def test_a_declared_hash_for_the_new_pin_marks_the_old_install_outdated_not_modified(self):
        self.publish_v1()
        self.apply()
        self.publish_v2()
        v2 = sha256(b"print('alpha v2')\n")
        self.consumer.write_lock(self.commit2, declared={"scripts/alpha.py": v2})
        code, doc = self.verify()
        self.assertEqual(self.file(doc, "scripts/alpha.py")["local"]["status"], "outdated")
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 0, doc["findings"])
        self.assertEqual(self.consumer.read("scripts/alpha.py"), b"print('alpha v2')\n")

    def test_identical_bytes_with_the_wrong_mode_and_no_receipt_are_a_conflict(self):
        self.publish_v1()
        self.consumer.write("scripts/run.sh", self.upstream.files["scripts/run.sh"], mode=0o644)  # pin: 100755
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 1)
        self.assertIn("CONFLICT_UNMANAGED", self.codes(doc, "error"))
        self.assertIn("another executable bit", self.file(doc, "scripts/run.sh")["reason"])
        self.assertEqual(self.consumer.mode("scripts/run.sh"), 0o644)
        self.assertFalse(self.consumer.exists("scripts/alpha.py"))

    def test_a_receipt_with_a_wrong_mode_is_corrected_without_touching_the_file(self):
        self.publish_v1()
        self.apply()
        receipt = self.consumer.receipt()
        for entry in receipt["files"]:
            if entry["path"] == "scripts/run.sh":
                entry["mode"] = "100644"
        with open(self.consumer.receipt_path, "w") as handle:
            json.dump(receipt, handle)
        os.utime(self.consumer.path("scripts/run.sh"), ns=(10 ** 9, 10 ** 9))
        code, doc = self.verify()
        self.assertEqual(code, 1)  # the receipt and the file disagree
        code, dry = self.materialize()
        self.assertEqual(self.file(dry, "scripts/run.sh")["action"], "record")
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 0, doc["findings"])
        self.assertEqual(doc["result"]["written"], [])
        self.assertEqual(os.lstat(self.consumer.path("scripts/run.sh")).st_mtime_ns, 10 ** 9)
        self.assertEqual({e["path"]: e["mode"] for e in self.consumer.receipt()["files"]}["scripts/run.sh"], "100755")
        self.assertEqual(self.verify()[0], 0)

    def test_a_dry_run_that_has_nothing_to_do_does_not_claim_to_have_applied(self):
        self.publish_v1()
        self.apply()
        code, doc = self.materialize()
        self.assertEqual(code, 0)
        self.assertEqual((doc["result"]["dry_run"], doc["result"]["applied"]), (True, False))
        code, doc = self.materialize("--apply")
        self.assertEqual((doc["result"]["dry_run"], doc["result"]["applied"]), (False, True))


class OnlyOutdatedFilesTests(ToolingCase):
    def test_outdated_files_alone_fail_local_integrity(self):
        self.publish_v1()
        self.apply()
        commit2 = self.upstream.publish({"scripts/alpha.py": "print('alpha v2')\n"}, executable=("scripts/run.sh",),
                                        message="v2 touches one file only")
        self.consumer.write_lock(commit2)
        code, doc = self.verify()
        self.assertEqual(code, 1)
        self.assertEqual(sorted((r["path"], r["local"]["status"]) for r in doc["files"]),
                         [("schemas/beta.schema.json", "ok"), ("scripts/alpha.py", "outdated"), ("scripts/run.sh", "ok")])
        self.assertEqual(doc["summary"]["local_integrity"], "failed")


class ModeOnlyChangeUpstreamTests(ToolingCase):
    def test_a_mode_only_change_in_the_pin_is_an_upgrade_not_a_pass(self):
        self.publish_v1()
        self.apply()
        commit2 = self.upstream.publish({}, unexecutable=("scripts/run.sh",), message="drop +x, same bytes")
        self.consumer.write_lock(commit2)
        code, doc = self.verify()
        self.assertEqual(code, 1)
        record = self.file(doc, "scripts/run.sh")
        self.assertEqual((record["local"]["status"], record["matches_pin"]), ("outdated", False))
        self.assertEqual(record["pinned"]["mode"], "100644")
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 0, doc["findings"])
        self.assertEqual(doc["result"]["written"], ["scripts/run.sh"])
        self.assertEqual(self.consumer.mode("scripts/run.sh"), 0o644)
        self.assertEqual(self.verify()[0], 0)


class ModeOfDeclaredFilesWithoutReceiptTests(ToolingCase):
    def test_a_declared_file_with_the_right_bytes_and_the_wrong_mode_is_not_ok_even_without_a_receipt(self):
        commit = self.publish_v1()
        digest = sha256(self.upstream.files["scripts/run.sh"])
        self.consumer.write_lock(commit, declared={"scripts/run.sh": digest})
        self.consumer.write("scripts/run.sh", self.upstream.files["scripts/run.sh"], mode=0o755)
        self.assertEqual(self.file(self.verify()[1], "scripts/run.sh")["local"]["status"], "ok")
        os.chmod(self.consumer.path("scripts/run.sh"), 0o644)
        code, doc = self.verify()
        self.assertEqual(self.file(doc, "scripts/run.sh")["local"]["status"], "modified")
        self.assertEqual(code, 1)

    def test_a_receipt_naming_a_different_blob_than_the_pin_is_a_provenance_failure(self):
        self.publish_v1()
        self.apply()
        receipt = self.consumer.receipt()
        receipt["files"][0]["git_blob"] = "e" * 40
        with open(self.consumer.receipt_path, "w") as handle:
            json.dump(receipt, handle)
        code, doc = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("PROVENANCE_MISMATCH", self.codes(doc, "error"))
        self.assertEqual(doc["summary"]["upstream_provenance"], "failed")
