"""Failure during installation, interruption, repetition and concurrency.

Proven here: a staging failure leaves every destination and the receipt
untouched; a failure after the first file was replaced, or a process killed
at that point, leaves an explicit `installing` journal that verify reports as
recovery-required and a repeated `materialize --apply` finishes; concurrent
runs are serialised or rejected. Staging files: a run that completes, or
fails safely, removes the staged copies it made itself (through the directory
descriptors it holds, and only while the name still refers to the file it
created); a run that is killed leaves its staged copies, which no later run
removes or reuses (they are kept and reported), because nothing but the name
says whose they are. No file is ever removed through a path string, so a
directory swapped for a symlink cannot redirect a removal.
"""
import errno
import json
import os
import stat
import shutil
import subprocess
import sys
import tempfile
import textwrap
from unittest import mock

from tooling_helpers import REPO_ROOT, TOOLING_PY, ToolingCase, clean_env, engine, sha256
from tooling_core import fsops


def stage_files(root):
    return sorted(os.path.join(d, n) for d, _, names in os.walk(root) for n in names
                  if n.startswith(".tooltempest-stage"))


def snapshot(paths):
    """Bytes and permission bits of each path: what "left untouched" is compared against."""
    result = {}
    for path in paths:
        with open(path, "rb") as handle:
            result[path] = (handle.read(), stat.S_IMODE(os.lstat(path).st_mode))
    return result


class RecoveryBase(ToolingCase):
    def setUp(self):
        super().setUp()
        self.publish_v1()
        self.apply()
        # v2: alpha.py upgrades, gamma and a new subdirectory file are created
        self.commit2 = self.upstream.publish(
            {"scripts/alpha.py": "print('alpha v2')\n", "schemas/gamma.schema.json": '{"v": 2}\n',
             "scripts/sub/mod.py": "mod\n"}, executable=("scripts/run.sh",), message="v2")
        self.consumer.write_lock(self.commit2)
        self.order = ["schemas/gamma.schema.json", "scripts/alpha.py", "scripts/sub/mod.py"]

    def interrupt_after(self, installed):
        """Run materialize in-process and fail the rename of file number `installed`+1."""
        real_replace = os.replace
        renames = []

        def failing(src, dst, **kw):
            if not dst.endswith(".tooltempest.receipt.json"):
                if len(renames) == installed:
                    raise OSError(errno.EIO, "injected I/O error")
                renames.append(dst)
            return real_replace(src, dst, **kw)

        with mock.patch.object(engine.os, "replace", side_effect=failing):
            return engine.run_materialize(self.engine_options(apply=True))


class StagingFailureTests(RecoveryBase):
    def test_a_failure_while_staging_changes_nothing_and_leaves_no_debris(self):
        before = self.digest(self.consumer.root)
        receipt_before = self.consumer.read(".tooltempest.receipt.json")
        real = fsops.create_exclusive
        calls = []

        def fail_on_the_third(directory, stem, data, mode):
            calls.append(stem)
            if len(calls) == 3:
                raise OSError(errno.ENOSPC, "no space left on device")
            return real(directory, stem, data, mode)

        with mock.patch.object(fsops, "create_exclusive", side_effect=fail_on_the_third):
            doc = engine.run_materialize(self.engine_options(apply=True))
        self.assertEqual(doc["summary"]["exit_code"], 1)
        self.assertIn("STAGING_FAILED", self.codes(doc, "error"))
        self.assertIn("no destination was modified", doc["findings"][-1]["message"])
        self.assertEqual(self.digest(self.consumer.root), before)
        self.assertEqual(self.consumer.read(".tooltempest.receipt.json"), receipt_before)
        self.assertEqual(stage_files(self.consumer.root), [])
        self.assertFalse(self.consumer.exists("scripts/sub"), "a directory created for the failed install remained")
        self.assertEqual(self.consumer.receipt()["state"], "complete")

    def test_a_failure_writing_the_journal_leaves_the_checkout_as_it_was(self):
        before = self.digest(self.consumer.root)
        real_replace = os.replace

        def fail_for_the_receipt(src, dst, **kw):
            if dst.endswith(".tooltempest.receipt.json"):
                raise OSError(errno.EACCES, "denied")
            return real_replace(src, dst, **kw)

        with mock.patch.object(engine.os, "replace", side_effect=fail_for_the_receipt):
            doc = engine.run_materialize(self.engine_options(apply=True))
        self.assertEqual(doc["summary"]["exit_code"], 1)
        self.assertIn("RECEIPT_WRITE_FAILED", self.codes(doc, "error"))
        self.assertEqual(self.digest(self.consumer.root), before)

    def test_a_real_permission_failure_is_reported_and_leaves_no_debris(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores directory permissions")
        os.makedirs(self.consumer.path("scripts/sub"))
        os.chmod(self.consumer.path("scripts/sub"), 0o555)
        self.addCleanup(os.chmod, self.consumer.path("scripts/sub"), 0o755)
        before_files = self.consumer.read("scripts/alpha.py")
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 1)
        self.assertIn("STAGING_FAILED", self.codes(doc, "error"))
        self.assertEqual(self.consumer.read("scripts/alpha.py"), before_files)
        self.assertEqual(stage_files(self.consumer.root), [])


class InterruptionTests(RecoveryBase):
    def assertJournalState(self, installed_paths):
        receipt = self.consumer.receipt()
        self.assertEqual((receipt["state"], receipt["commit"]), ("installing", self.commit2))
        previous = {f["path"]: f["previous_sha256"] for f in receipt["files"]}
        self.assertEqual(previous["scripts/alpha.py"], sha256(b"print('alpha v1')\n"))
        self.assertIsNone(previous["schemas/gamma.schema.json"])  # did not exist before
        self.assertEqual(previous["scripts/run.sh"], sha256(self.upstream.files["scripts/run.sh"]))
        self.assertReceiptValid()
        for path in self.order:
            new = path in installed_paths
            if path == "scripts/alpha.py":
                expected = b"print('alpha v2')\n" if new else b"print('alpha v1')\n"
                self.assertEqual(self.consumer.read(path), expected, path)
            else:
                self.assertEqual(self.consumer.exists(path), new, path)

    def test_a_failure_after_the_first_file_was_installed_leaves_an_explicit_recovery_state(self):
        doc = self.interrupt_after(1)
        self.assertEqual(doc["summary"]["exit_code"], 1)
        failure = [f for f in doc["findings"] if f["code"] == "INSTALL_INTERRUPTED"][0]
        self.assertIn("after 1 of 3 file(s)", failure["message"])
        self.assertIn("recovery required", failure["hint"])
        self.assertIn("materialize", failure["hint"])
        self.assertEqual(doc["result"]["written"], [self.order[0]])
        self.assertJournalState({self.order[0]})
        self.assertEqual(stage_files(self.consumer.root), [], "staged copies of the unfinished files remained")

    def test_verify_and_status_report_the_unfinished_installation_and_do_not_pass(self):
        self.interrupt_after(1)
        code, doc = self.verify()
        self.assertEqual(code, 1)
        self.assertEqual(doc["receipt"]["state"], "installing")
        self.assertIn("RECOVERY_REQUIRED", self.codes(doc, "error"))
        self.assertEqual(doc["summary"]["local_integrity"], "failed")
        self.assertEqual(self.file(doc, "schemas/gamma.schema.json")["local"]["status"], "ok")
        self.assertEqual(self.file(doc, "scripts/alpha.py")["local"]["status"], "stale")
        self.assertEqual(self.file(doc, "scripts/sub/mod.py")["local"]["status"], "missing")
        code, status = self.run_json("status")
        self.assertEqual(code, 0)
        self.assertIn("RECOVERY_REQUIRED", self.codes(status, "error"))

    def test_a_repeated_apply_finishes_the_interrupted_installation(self):
        self.interrupt_after(1)
        code, dry = self.materialize()
        self.assertEqual(code, 0)
        self.assertEqual({r["path"]: r["action"] for r in dry["files"] if r["action"] in ("create", "upgrade")},
                         {"scripts/alpha.py": "upgrade", "scripts/sub/mod.py": "create"})
        self.assertEqual(self.file(dry, "schemas/gamma.schema.json")["action"], "record")
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 0, doc["findings"])
        self.assertEqual(sorted(doc["result"]["written"]), ["scripts/alpha.py", "scripts/sub/mod.py"])
        self.assertEqual(self.consumer.receipt()["state"], "complete")
        self.assertEqual(self.verify()[0], 0)

    def test_a_process_killed_between_two_renames_is_recoverable(self):
        script = textwrap.dedent('''
            import os, sys
            sys.dont_write_bytecode = True
            sys.path.insert(0, %(scripts)r)
            real = os.replace
            calls = []
            def die_on_the_third(src, dst, **kw):
                calls.append(dst)
                if len(calls) == 3:  # 1 = the journal, 2 = the first file, 3 = the second
                    os._exit(137)
                return real(src, dst, **kw)
            os.replace = die_on_the_third
            from tooling_core.cli import main
            sys.exit(main(sys.argv[1:]))
        ''' % {"scripts": str(REPO_ROOT / "scripts")})
        proc = subprocess.run([sys.executable, "-I", "-c", script, *self.args("materialize", "--apply")],
                              capture_output=True, env=clean_env({"HOME": self.home}))
        self.assertEqual(proc.returncode, 137)
        self.assertJournalState({self.order[0]})
        leftovers = snapshot(stage_files(self.consumer.root))
        self.assertNotEqual(leftovers, {}, "the kill should have left staged copies")
        code, doc = self.verify()
        self.assertEqual(code, 1)
        self.assertIn("RECOVERY_REQUIRED", self.codes(doc, "error"))
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 0, doc["findings"])
        # the tool cannot prove by name that these are its own: they are kept, and said so
        self.assertEqual(snapshot(stage_files(self.consumer.root)), leftovers)
        self.assertEqual(self.codes(doc, "warning").count("STAGING_FILE_PRESERVED"), len(leftovers))
        self.assertEqual(self.consumer.receipt()["state"], "complete")
        self.assertEqual(self.verify()[0], 0)

    def test_a_process_killed_before_the_journal_changes_nothing_that_matters(self):
        script = textwrap.dedent('''
            import os, sys
            sys.dont_write_bytecode = True
            sys.path.insert(0, %(scripts)r)
            real = os.replace
            def die_on_the_journal(src, dst, **kw):
                os._exit(137)
            os.replace = die_on_the_journal
            from tooling_core.cli import main
            sys.exit(main(sys.argv[1:]))
        ''' % {"scripts": str(REPO_ROOT / "scripts")})
        proc = subprocess.run([sys.executable, "-I", "-c", script, *self.args("materialize", "--apply")],
                              capture_output=True, env=clean_env({"HOME": self.home}))
        self.assertEqual(proc.returncode, 137)
        self.assertEqual(self.consumer.receipt()["state"], "complete")  # the old receipt was never replaced
        self.assertEqual(self.consumer.read("scripts/alpha.py"), b"print('alpha v1')\n")
        leftovers = snapshot(stage_files(self.consumer.root))
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 0, doc["findings"])
        self.assertEqual(snapshot(stage_files(self.consumer.root)), leftovers)
        self.assertEqual(self.verify()[0], 0)

    def test_a_file_edited_during_the_interrupted_state_is_refused_not_overwritten(self):
        self.interrupt_after(1)
        self.consumer.write("scripts/alpha.py", "edited while the install was unfinished\n", mode=0o700)
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 1)
        self.assertIn("CONFLICT_MANAGED_MODIFIED", self.codes(doc, "error"))
        self.assertEqual(self.consumer.read("scripts/alpha.py"), b"edited while the install was unfinished\n")
        self.assertEqual(self.consumer.mode("scripts/alpha.py"), 0o700)
        self.assertEqual(self.consumer.receipt()["state"], "installing")
        code, vdoc = self.verify()
        self.assertEqual(self.file(vdoc, "scripts/alpha.py")["local"]["status"], "modified")

    def test_recovery_may_finish_towards_a_different_pin(self):
        self.interrupt_after(1)
        commit3 = self.upstream.publish({"scripts/alpha.py": "print('alpha v3')\n"}, message="v3")
        self.consumer.write_lock(commit3)
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 0, doc["findings"])
        self.assertEqual(self.consumer.read("scripts/alpha.py"), b"print('alpha v3')\n")
        self.assertEqual(self.consumer.receipt()["commit"], commit3)
        self.assertEqual(self.verify()[0], 0)


class ConcurrencyTests(RecoveryBase):
    def lock_file(self):
        return os.path.join(self.consumer.fx.git("rev-parse", "--absolute-git-dir"), "tooltempest-install.lock")

    def test_an_apply_is_rejected_while_another_holds_the_lock_and_changes_nothing(self):
        holder = subprocess.Popen(
            [sys.executable, "-c", textwrap.dedent('''
                import fcntl, os, sys
                fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX)
                print("locked", flush=True)
                sys.stdin.read()
            '''), self.lock_file()], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            self.assertEqual(holder.stdout.readline().strip(), b"locked")
            before = self.digest(self.consumer.root)
            code, doc = self.materialize("--apply")
            self.assertEqual(code, 1)
            self.assertIn("INSTALL_LOCKED", self.codes(doc, "error"))
            self.assertEqual(doc["result"]["written"], [])
            self.assertEqual(self.digest(self.consumer.root), before)
            # a run with nothing to write never needs the lock
            self.assertEqual(self.materialize()[0], 0)
        finally:
            holder.stdin.close()
            holder.wait(timeout=30)
            holder.stdout.close()
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 0, doc["findings"])

    def test_many_simultaneous_applies_end_in_one_consistent_complete_installation(self):
        procs = [subprocess.Popen([sys.executable, "-I", str(TOOLING_PY), *self.args("materialize", "--apply")],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=clean_env({"HOME": self.home})) for _ in range(6)]
        outcomes = []
        for proc in procs:
            out, err = proc.communicate(timeout=300)
            outcomes.append(proc.returncode)
            doc = json.loads(out)
            if proc.returncode == 1:
                self.assertEqual(self.codes(doc, "error"), ["INSTALL_LOCKED"], doc["findings"])
            else:
                self.assertEqual(proc.returncode, 0, err)
        self.assertIn(0, outcomes)
        self.assertEqual(self.consumer.receipt()["state"], "complete")
        self.assertEqual(self.consumer.read("scripts/alpha.py"), b"print('alpha v2')\n")
        self.assertEqual(stage_files(self.consumer.root), [])
        self.assertEqual(self.verify()[0], 0)

    def test_the_lock_is_released_when_the_holder_dies(self):
        holder = subprocess.Popen(
            [sys.executable, "-c", textwrap.dedent('''
                import fcntl, os, sys
                fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX)
                print("locked", flush=True)
                os._exit(9)
            '''), self.lock_file()], stdout=subprocess.PIPE)
        holder.stdout.readline()
        holder.wait(timeout=30)
        holder.stdout.close()
        self.assertEqual(self.materialize("--apply")[0], 0)


class ChangedUnderneathTests(RecoveryBase):
    def test_a_file_edited_after_planning_but_before_staging_is_not_overwritten(self):
        real_execute = engine._execute

        def edit_then_execute(ev, pending, final, receipt_changes):
            self.consumer.write("scripts/alpha.py", "edited in the gap\n", mode=0o700)
            return real_execute(ev, pending, final, receipt_changes)

        before_receipt = self.consumer.read(".tooltempest.receipt.json")
        with mock.patch.object(engine, "_execute", side_effect=edit_then_execute):
            doc = engine.run_materialize(self.engine_options(apply=True))
        self.assertEqual(doc["summary"]["exit_code"], 1)
        self.assertIn("DESTINATION_CHANGED", self.codes(doc, "error"))
        self.assertEqual(self.consumer.read("scripts/alpha.py"), b"edited in the gap\n")
        self.assertEqual(self.consumer.mode("scripts/alpha.py"), 0o700)
        self.assertEqual(self.consumer.read(".tooltempest.receipt.json"), before_receipt)
        self.assertFalse(self.consumer.exists("schemas/gamma.schema.json"))
        self.assertEqual(stage_files(self.consumer.root), [])

    def test_a_file_edited_after_the_journal_but_before_its_rename_is_not_overwritten(self):
        real_replace = os.replace
        edited = []

        def edit_after_the_journal(src, dst, **kw):
            real_replace(src, dst, **kw)
            if dst == ".tooltempest.receipt.json" and not edited:
                edited.append(True)
                self.consumer.write("scripts/alpha.py", "edited after the journal\n", mode=0o700)

        with mock.patch.object(engine.os, "replace", side_effect=edit_after_the_journal):
            doc = engine.run_materialize(self.engine_options(apply=True))
        self.assertEqual(doc["summary"]["exit_code"], 1)
        self.assertIn("INSTALL_INTERRUPTED", self.codes(doc, "error"))
        self.assertIn("changed during the installation", " ".join(f["message"] for f in doc["findings"]))
        self.assertEqual(self.consumer.read("scripts/alpha.py"), b"edited after the journal\n")
        self.assertEqual(self.consumer.mode("scripts/alpha.py"), 0o700)
        self.assertEqual(stage_files(self.consumer.root), [])


class PostInstallVerificationTests(RecoveryBase):
    def test_an_installation_that_does_not_verify_afterwards_is_reported_as_a_failure(self):
        real = engine.properties
        calls = []

        def lie_the_third_time(ev):
            calls.append(1)
            return ("failed", "ok") if len(calls) == 3 else real(ev)

        with mock.patch.object(engine, "properties", side_effect=lie_the_third_time):
            doc = engine.run_materialize(self.engine_options(apply=True))
        self.assertEqual(doc["summary"]["exit_code"], 1)
        self.assertIn("POST_INSTALL_VERIFICATION_FAILED", self.codes(doc, "error"))
        self.assertFalse(doc["result"]["post_verification"]["ok"])
        self.assertTrue(doc["result"]["applied"])


class StaleTemporaryFileTests(RecoveryBase):
    def test_a_receipt_temporary_file_left_by_a_crashed_run_is_kept_and_does_not_block_the_next_one(self):
        stale = os.path.join(self.consumer.root, "..tooltempest.receipt.json.tmp.%d" % os.getpid())  # ".%s.tmp.%d"
        with open(stale, "w") as handle:
            handle.write("left behind")
        os.chmod(stale, 0o640)
        before = snapshot([stale])
        doc = engine.run_materialize(self.engine_options(apply=True))
        self.assertEqual(doc["summary"]["exit_code"], 0, doc["findings"])
        self.assertEqual(snapshot([stale]), before)
        self.assertIn("..tooltempest.receipt.json.tmp.%d" % os.getpid(),
                      [f["path"] for f in doc["findings"] if f["code"] == "STAGING_FILE_PRESERVED"])
        self.assertEqual(self.consumer.receipt()["state"], "complete")


class OnlyStaleFilesTests(ToolingCase):
    def test_a_stale_file_alone_fails_local_integrity(self):
        self.publish_v1()
        self.apply()
        commit2 = self.upstream.publish({"scripts/alpha.py": "print('alpha v2')\n"}, executable=("scripts/run.sh",),
                                        message="v2 touches one file only")
        self.consumer.write_lock(commit2)
        real_replace = os.replace

        def fail_the_only_rename(src, dst, **kw):
            if not dst.endswith(".tooltempest.receipt.json"):
                raise OSError(errno.EIO, "injected")
            return real_replace(src, dst, **kw)

        with mock.patch.object(engine.os, "replace", side_effect=fail_the_only_rename):
            doc = engine.run_materialize(self.engine_options(apply=True))
        self.assertEqual(doc["summary"]["exit_code"], 1)
        code, vdoc = self.verify()
        self.assertEqual(code, 1)
        self.assertEqual(sorted((r["path"], r["local"]["status"]) for r in vdoc["files"]),
                         [("schemas/beta.schema.json", "ok"), ("scripts/alpha.py", "stale"), ("scripts/run.sh", "ok")])
        self.assertEqual(vdoc["summary"]["local_integrity"], "failed")


class ModeEditDuringInterruptedStateTests(RecoveryBase):
    def test_a_mode_only_edit_of_a_managed_file_is_refused_in_the_interrupted_state_too(self):
        self.interrupt_after(1)  # gamma is installed (0644); alpha still has its v1 bytes
        for path, new_mode in (("schemas/gamma.schema.json", 0o755), ("scripts/alpha.py", 0o755)):
            with self.subTest(path):
                original = self.consumer.mode(path)
                os.chmod(self.consumer.path(path), new_mode)
                receipt_before = self.consumer.read(".tooltempest.receipt.json")
                code, vdoc = self.verify()
                self.assertEqual(self.file(vdoc, path)["local"]["status"], "modified")
                code, doc = self.materialize("--apply")
                self.assertEqual(code, 1)
                self.assertIn("CONFLICT_MANAGED_MODIFIED", self.codes(doc, "error"))
                self.assertEqual(self.consumer.mode(path), new_mode)
                self.assertEqual(self.consumer.read(".tooltempest.receipt.json"), receipt_before)
                self.assertFalse(self.consumer.exists("scripts/sub/mod.py"), "other files were installed")
                os.chmod(self.consumer.path(path), original)  # undo, so the next subtest starts clean

    def test_the_journal_records_the_previous_mode_of_each_file(self):
        self.interrupt_after(1)
        modes = {f["path"]: (f["previous_sha256"], f["previous_mode"]) for f in self.consumer.receipt()["files"]}
        self.assertEqual(modes["scripts/run.sh"][1], "100755")
        self.assertEqual(modes["scripts/alpha.py"][1], "100644")
        self.assertEqual(modes["schemas/gamma.schema.json"], (None, None))


class FinalReceiptFailureTests(RecoveryBase):
    def test_a_failure_writing_the_complete_receipt_reports_what_was_installed_and_how_to_recover(self):
        real_replace = os.replace
        receipt_writes = []

        def fail_the_second_receipt_write(src, dst, **kw):
            if dst.endswith(".tooltempest.receipt.json"):
                receipt_writes.append(dst)
                if len(receipt_writes) == 2:  # 1 = the journal, 2 = the final receipt
                    raise OSError(errno.EIO, "injected")
            return real_replace(src, dst, **kw)

        with mock.patch.object(engine.os, "replace", side_effect=fail_the_second_receipt_write):
            doc = engine.run_materialize(self.engine_options(apply=True))
        self.assertEqual(doc["summary"]["exit_code"], 1)
        failure = [f for f in doc["findings"] if f["code"] == "RECEIPT_WRITE_FAILED"][0]
        self.assertIn("3 file(s) were installed", failure["message"])
        self.assertIn("recovery required", failure["hint"])
        self.assertEqual(sorted(doc["result"]["written"]), sorted(self.order))
        self.assertEqual(self.consumer.receipt()["state"], "installing")
        code, vdoc = self.verify()
        self.assertIn("RECOVERY_REQUIRED", self.codes(vdoc, "error"))
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 0, doc["findings"])
        self.assertEqual(doc["result"]["written"], [])  # only the receipt was left to do
        self.assertEqual(self.verify()[0], 0)


FAKE_PID = 424242
USER_BYTES = b"precious user data, not written by the tool\n"


class UserFilesWithStagingNamesTests(RecoveryBase):
    """A file named exactly like a staging file is never overwritten or deleted: the
    tests plant one at every name the tool would pick first, so the collision is
    certain and does not depend on the name being unlikely."""

    def plant(self, mode=0o640, with_receipt_tmp=True):
        paths = [self.consumer.path("%s/%s%d.%d" % (directory, engine.STAGE_PREFIX, FAKE_PID, number))
                 for directory in ("scripts", "schemas") for number in range(len(self.order))]
        if with_receipt_tmp:
            paths.append(self.consumer.path("..tooltempest.receipt.json.tmp.%d" % FAKE_PID))
        for path in paths:
            with open(path, "wb") as handle:
                handle.write(USER_BYTES)
            os.chmod(path, mode)
        return snapshot(paths)

    def pid(self):
        return mock.patch.object(engine.os, "getpid", return_value=FAKE_PID)

    def assertUntouched(self, planted):
        self.assertEqual(snapshot(planted), planted)
        self.assertEqual(sorted(stage_files(self.consumer.root)), sorted(p for p in planted
                                                                         if ".tooltempest-stage." in p),
                         "the tool left a staging file of its own behind")

    def test_materialize_never_overwrites_a_user_file_at_a_staging_name_and_still_installs(self):
        planted = self.plant()
        real, attempted = fsops._write, []

        def spy(directory, name, data, mode):
            attempted.append(os.path.join(directory.label(), name))
            return real(directory, name, data, mode)

        with self.pid(), mock.patch.object(fsops, "_write", side_effect=spy):
            doc = engine.run_materialize(self.engine_options(apply=True))
        # the collision is real: the tool tried the very names the user's files have
        for collided in ("schemas/%s%d.0" % (engine.STAGE_PREFIX, FAKE_PID),
                         "scripts/%s%d.1" % (engine.STAGE_PREFIX, FAKE_PID),
                         os.path.join(os.path.realpath(self.consumer.root), "..tooltempest.receipt.json.tmp.%d" % FAKE_PID)):
            self.assertIn(collided, attempted)
        self.assertEqual(doc["summary"]["exit_code"], 0, doc["findings"])
        self.assertUntouched(planted)
        self.assertEqual(sorted(f["path"] for f in doc["findings"] if f["code"] == "STAGING_FILE_PRESERVED"),
                         sorted(os.path.relpath(p, self.consumer.root) for p in planted))
        self.assertTrue(all(f["severity"] == "warning" and "by hand" in f["hint"] for f in doc["findings"]
                            if f["code"] == "STAGING_FILE_PRESERVED"))
        self.assertEqual(self.consumer.read("scripts/alpha.py"), b"print('alpha v2')\n")
        self.assertEqual(self.consumer.receipt()["state"], "complete")
        self.assertEqual(self.verify()[0], 0)

    def test_only_names_that_start_like_a_temporary_file_are_reported(self):
        for name in ("scripts/x%s1.2" % engine.STAGE_PREFIX, "x..tooltempest.receipt.json.tmp.1"):
            with open(self.consumer.path(name), "wb") as handle:
                handle.write(USER_BYTES)
        doc = engine.run_materialize(self.engine_options(apply=True))
        self.assertEqual(doc["summary"]["exit_code"], 0, doc["findings"])
        self.assertEqual([f for f in doc["findings"] if f["code"] == "STAGING_FILE_PRESERVED"], [])

    def test_a_dry_run_reports_the_files_and_touches_nothing(self):
        planted = self.plant()
        before = self.digest(self.consumer.root)
        with self.pid():
            doc = engine.run_materialize(self.engine_options(apply=False))
        self.assertEqual(doc["summary"]["exit_code"], 0)
        self.assertEqual(self.digest(self.consumer.root), before)
        self.assertEqual(snapshot(planted), planted, "bytes and modes of the user's files")
        self.assertEqual(len([f for f in doc["findings"] if f["code"] == "STAGING_FILE_PRESERVED"]), len(planted))

    def test_cleanup_after_a_staging_failure_removes_only_what_the_run_created(self):
        planted = self.plant()
        before = self.digest(self.consumer.root)
        real = fsops.create_exclusive
        made = []

        def fail_the_third_creation(directory, stem, data, mode):
            if len(made) == 2:
                raise OSError(errno.ENOSPC, "no space left on device")
            created = real(directory, stem, data, mode)  # takes another name when one is taken
            made.append(os.path.join(self.consumer.root, directory.label(), created.name))
            return created

        with self.pid(), mock.patch.object(fsops, "create_exclusive", side_effect=fail_the_third_creation):
            doc = engine.run_materialize(self.engine_options(apply=True))
        self.assertIn("STAGING_FAILED", self.codes(doc, "error"))
        self.assertEqual(len(made), 2)
        self.assertTrue(all(not os.path.exists(path) for path in made), "the run's own staged copies remained")
        self.assertUntouched(planted)
        self.assertEqual(self.digest(self.consumer.root), before)

    def test_cleanup_after_an_interrupted_rename_removes_only_what_the_run_created(self):
        planted = self.plant()
        with self.pid():
            doc = self.interrupt_after(1)
        self.assertIn("INSTALL_INTERRUPTED", self.codes(doc, "error"))
        self.assertUntouched(planted)

    def test_a_failed_receipt_write_removes_only_the_receipt_file_the_run_created(self):
        planted = self.plant()
        real_replace = os.replace

        def fail_for_the_receipt(src, dst, **kw):
            if dst.endswith(".tooltempest.receipt.json"):
                raise OSError(errno.EACCES, "denied")
            return real_replace(src, dst, **kw)

        with self.pid(), mock.patch.object(engine.os, "replace", side_effect=fail_for_the_receipt):
            doc = engine.run_materialize(self.engine_options(apply=True))
        self.assertIn("RECEIPT_WRITE_FAILED", self.codes(doc, "error"))
        self.assertEqual(snapshot(planted), planted)
        leftovers = [n for n in os.listdir(self.consumer.root) if n.startswith("..tooltempest.receipt.json.tmp.")]
        self.assertEqual(leftovers, ["..tooltempest.receipt.json.tmp.%d" % FAKE_PID])

    def test_cleanup_after_a_failed_rename_leaves_a_file_that_appeared_at_an_already_renamed_staging_name(self):
        real_replace = os.replace
        renamed, newcomer = [], {}

        def rename_then_fail_the_second(src, dst, **kw):
            if dst.endswith(".tooltempest.receipt.json"):
                return real_replace(src, dst, **kw)
            if len(renamed) == 1:
                raise OSError(errno.EIO, "injected I/O error")
            real_replace(src, dst, **kw)
            renamed.append(src)
            where = self.consumer.path(os.path.join(os.path.dirname(self.order[0]), src))
            with open(where, "wb") as handle:  # the staging name is free again: someone else takes it
                handle.write(USER_BYTES)
            os.chmod(where, 0o640)
            newcomer.update(snapshot([where]))

        with mock.patch.object(engine.os, "replace", side_effect=rename_then_fail_the_second):
            doc = engine.run_materialize(self.engine_options(apply=True))
        self.assertIn("INSTALL_INTERRUPTED", self.codes(doc, "error"))
        self.assertEqual(len(newcomer), 1)
        self.assertEqual(snapshot(newcomer), newcomer)

    def test_a_file_that_cannot_be_filled_is_removed_but_one_that_was_in_the_way_is_not(self):
        directory = self.consumer.path("scripts")
        taken = os.path.join(directory, "taken")
        with open(taken, "wb") as handle:
            handle.write(USER_BYTES)
        os.chmod(taken, 0o640)
        before = snapshot([taken])
        tree = fsops.Tree(self.consumer.root)
        self.addCleanup(tree.close)
        scripts = tree.descend(tree.root, "scripts")
        with self.assertRaises(FileExistsError):
            fsops._write(scripts, "taken", b"other bytes", "100644")
        self.assertEqual(snapshot([taken]), before)
        with mock.patch.object(fsops.os, "fchmod", side_effect=OSError(errno.EPERM, "denied")):
            with self.assertRaises(OSError):
                fsops._write(scripts, "own", b"x", "100644")
        self.assertFalse(os.path.exists(os.path.join(directory, "own")),
                         "a file this call created and could not finish was left behind")

    def test_when_every_candidate_name_is_taken_nothing_is_replaced_and_nothing_is_installed(self):
        stem = "%s%d.0" % (engine.STAGE_PREFIX, FAKE_PID)
        planted = [self.consumer.path("schemas/" + stem)] + [
            self.consumer.path("schemas/%s.%d" % (stem, k)) for k in range(1, 64)]
        for path in planted:
            with open(path, "wb") as handle:
                handle.write(USER_BYTES)
            os.chmod(path, 0o640)
        before = self.digest(self.consumer.root)
        before_files = snapshot(planted)
        with self.pid():
            doc = engine.run_materialize(self.engine_options(apply=True))
        self.assertIn("STAGING_FAILED", self.codes(doc, "error"))
        self.assertEqual(doc["summary"]["exit_code"], 1)
        self.assertEqual(self.digest(self.consumer.root), before)
        self.assertEqual(snapshot(planted), before_files, "bytes and modes of the user's files")

    def test_recovery_after_a_kill_keeps_every_file_it_cannot_prove_is_its_own(self):
        planted = self.plant()
        script = textwrap.dedent('''
            import os, sys
            sys.dont_write_bytecode = True
            sys.path.insert(0, %(scripts)r)
            os.getpid = lambda: %(pid)d
            real = os.replace
            calls = []
            def die_on_the_third(src, dst, **kw):
                calls.append(dst)
                if len(calls) == 3:  # 1 = the journal, 2 = the first file, 3 = the second
                    os._exit(137)
                return real(src, dst, **kw)
            os.replace = die_on_the_third
            from tooling_core.cli import main
            sys.exit(main(sys.argv[1:]))
        ''' % {"scripts": str(REPO_ROOT / "scripts"), "pid": FAKE_PID})
        proc = subprocess.run([sys.executable, "-I", "-c", script, *self.args("materialize", "--apply")],
                              capture_output=True, env=clean_env({"HOME": self.home}))
        self.assertEqual(proc.returncode, 137)
        self.assertEqual(self.consumer.receipt()["state"], "installing")
        self.assertEqual(snapshot(planted), planted)
        # the killed run's own leftovers cannot be told from the user's files, so they join the set
        everything = snapshot(stage_files(self.consumer.root) + [p for p in planted if ".tmp." in p])
        self.assertGreater(len(everything), len(planted), "the kill should have left staged copies of its own")
        with self.pid():
            doc = engine.run_materialize(self.engine_options(apply=True))
        self.assertEqual(doc["summary"]["exit_code"], 0, doc["findings"])
        self.assertEqual(snapshot(everything), everything)
        self.assertEqual(self.consumer.receipt()["state"], "complete")
        self.assertEqual(self.verify()[0], 0)


class SwappedDirectoryTests(RecoveryBase):
    """A directory on the way to a staged file is replaced by a symlink while the run is
    in progress, AFTER the run's own path checks passed, and a file named exactly like a
    staging file (known bytes and mode) already sits in the directory the link points at.
    Every cleanup branch must leave that file, and the empty directory next to it, as they
    were: the run acts through the directories it opened, not through the path strings."""

    def setUp(self):
        super().setUp()
        self.outside = tempfile.mkdtemp(prefix="tooling-outside-")
        self.addCleanup(shutil.rmtree, self.outside, ignore_errors=True)

    def plant_outside(self, names):
        paths = [os.path.join(self.outside, name) for name in names]
        for path in paths:
            with open(path, "wb") as handle:
                handle.write(USER_BYTES)
            os.chmod(path, 0o640)
        os.mkdir(os.path.join(self.outside, "sub"), 0o750)  # a user's empty directory with the created one's name
        os.chmod(os.path.join(self.outside, "sub"), 0o750)
        return snapshot(paths), os.stat(os.path.join(self.outside, "sub"))

    def swap(self, name):
        os.rename(self.consumer.path(name), self.consumer.path(name + ".real"))
        os.symlink(self.outside, self.consumer.path(name))

    def assertOutsideIntact(self, planted, sub_before):
        self.assertEqual(snapshot(planted), planted)
        info = os.stat(os.path.join(self.outside, "sub"))
        self.assertEqual((info.st_ino, stat.S_IMODE(info.st_mode)), (sub_before.st_ino, stat.S_IMODE(sub_before.st_mode)))
        self.assertEqual(sorted(os.listdir(self.outside)), sorted([os.path.basename(p) for p in planted] + ["sub"]),
                         "something was written into or removed from the directory the link points at")

    def scripts_names(self):
        return [".tooltempest-stage.%d.1" % FAKE_PID]

    def test_cleanup_after_a_failed_journal_write_does_not_delete_through_a_swapped_directory(self):
        planted, sub = self.plant_outside(self.scripts_names())
        real_replace = os.replace

        def swap_then_fail_the_journal(src, dst, **kw):
            if dst == ".tooltempest.receipt.json":
                self.swap("scripts")  # after every path check of this write has passed
                raise OSError(errno.EIO, "injected I/O error")
            return real_replace(src, dst, **kw)

        with patch_pid(), mock.patch.object(engine.os, "replace", side_effect=swap_then_fail_the_journal):
            doc = engine.run_materialize(self.engine_options(apply=True))
        self.assertIn("RECEIPT_WRITE_FAILED", self.codes(doc, "error"))
        self.assertOutsideIntact(planted, sub)
        # what the run itself made, in the directory it really opened, is gone
        self.assertEqual(os.listdir(self.consumer.path("scripts.real")).count("sub"), 0)
        self.assertEqual([n for n in os.listdir(self.consumer.path("scripts.real"))
                          if n.startswith(".tooltempest-stage")], [])
        self.assertEqual(self.consumer.receipt()["state"], "complete")

    def test_cleanup_after_a_failed_rename_does_not_delete_through_a_swapped_directory(self):
        planted, sub = self.plant_outside(self.scripts_names())
        real_replace = os.replace
        renames = []

        def swap_then_fail_the_second_file(src, dst, **kw):
            if dst == ".tooltempest.receipt.json":
                return real_replace(src, dst, **kw)
            renames.append(dst)
            if len(renames) == 2:  # the checks for this file passed; now the directory is swapped
                self.swap("scripts")
                raise OSError(errno.EIO, "injected I/O error")
            return real_replace(src, dst, **kw)

        with patch_pid(), mock.patch.object(engine.os, "replace", side_effect=swap_then_fail_the_second_file):
            doc = engine.run_materialize(self.engine_options(apply=True))
        self.assertIn("INSTALL_INTERRUPTED", self.codes(doc, "error"))
        self.assertOutsideIntact(planted, sub)
        self.assertEqual([n for n in os.listdir(self.consumer.path("scripts.real"))
                          if n.startswith(".tooltempest-stage")], [])
        self.assertEqual(self.consumer.receipt()["state"], "installing")

    def test_a_swap_during_staging_stops_safely_and_cleans_up_what_the_run_made(self):
        planted, sub = self.plant_outside(self.scripts_names())
        real = fsops.Tree.descend
        opened = []

        def swap_before_scripts_is_opened_again(tree, parent, name, create=False):
            opened.append(name)
            if name == "scripts" and opened.count("scripts") == 2:
                self.swap("scripts")  # the third file's path check has passed; now the directory changes
            return real(tree, parent, name, create)

        with patch_pid(), mock.patch.object(fsops.Tree, "descend", autospec=True,
                                            side_effect=swap_before_scripts_is_opened_again):
            doc = engine.run_materialize(self.engine_options(apply=True))
        self.assertIn("STAGING_FAILED", self.codes(doc, "error"))
        self.assertIn("symlink", " ".join(f["message"] for f in doc["findings"]))
        self.assertOutsideIntact(planted, sub)
        self.assertEqual(stage_files(self.consumer.root), [], "the run's own staged copy remained")
        self.assertEqual(self.consumer.receipt()["state"], "complete")

    def test_a_swapped_receipt_directory_is_not_deleted_through(self):
        os.mkdir(self.consumer.path("meta"))
        shutil.move(self.consumer.receipt_path, self.consumer.path("meta/.tooltempest.receipt.json"))
        name = "..tooltempest.receipt.json.tmp.%d" % FAKE_PID
        planted = os.path.join(self.outside, name)
        with open(planted, "wb") as handle:
            handle.write(USER_BYTES)
        os.chmod(planted, 0o640)
        before = snapshot([planted])
        real_replace = os.replace

        def swap_then_fail(src, dst, **kw):
            if dst == ".tooltempest.receipt.json":
                self.swap("meta")
                raise OSError(errno.EIO, "injected I/O error")
            return real_replace(src, dst, **kw)

        opts = self.engine_options(apply=True, receipt_path=self.consumer.path("meta/.tooltempest.receipt.json"))
        with patch_pid(), mock.patch.object(engine.os, "replace", side_effect=swap_then_fail):
            doc = engine.run_materialize(opts)
        self.assertIn("RECEIPT_WRITE_FAILED", self.codes(doc, "error"))
        self.assertEqual(snapshot([planted]), before)
        self.assertEqual(os.listdir(self.outside), [name])
        self.assertEqual([n for n in os.listdir(self.consumer.path("meta.real")) if ".tmp." in n], [])

    def test_a_receipt_directory_swapped_before_the_replace_is_not_written_through(self):
        os.mkdir(self.consumer.path("meta"))
        shutil.move(self.consumer.receipt_path, self.consumer.path("meta/.tooltempest.receipt.json"))
        before_receipt = self.consumer.read("meta/.tooltempest.receipt.json")
        real = fsops.create_exclusive
        swapped = []

        def swap_after_the_receipt_temp_is_created(directory, stem, data, mode):
            created = real(directory, stem, data, mode)
            if stem.startswith("..tooltempest.receipt") and not swapped:
                swapped.append(True)
                self.swap("meta")  # before the run's check of that directory, which must now fail
            return created

        opts = self.engine_options(apply=True, receipt_path=self.consumer.path("meta/.tooltempest.receipt.json"))
        with patch_pid(), mock.patch.object(fsops, "create_exclusive", side_effect=swap_after_the_receipt_temp_is_created):
            doc = engine.run_materialize(opts)
        self.assertIn("RECEIPT_WRITE_FAILED", self.codes(doc, "error"))
        self.assertEqual(os.listdir(self.outside), [])
        with open(self.consumer.path("meta.real/.tooltempest.receipt.json"), "rb") as handle:
            self.assertEqual(handle.read(), before_receipt, "the receipt was replaced although its directory changed")
        self.assertEqual([n for n in os.listdir(self.consumer.path("meta.real")) if ".tmp." in n], [])

    def test_without_the_primitives_for_safe_operation_it_fails_and_writes_nothing(self):
        planted, sub = self.plant_outside(self.scripts_names())
        before = self.digest(self.consumer.root)
        with mock.patch.object(fsops.os, "supports_dir_fd", set()):
            doc = engine.run_materialize(self.engine_options(apply=True))
        self.assertEqual(doc["summary"]["exit_code"], 1)
        self.assertIn("UNSUPPORTED_PLATFORM", self.codes(doc, "error"))
        self.assertIn("nothing was written", " ".join(f["message"] for f in doc["findings"]))
        self.assertEqual(self.digest(self.consumer.root), before)
        self.assertOutsideIntact(planted, sub)

    def test_a_root_replaced_by_a_symlink_before_the_run_opens_it_is_refused(self):
        link = self.consumer.root + ".link"
        os.symlink(self.consumer.root, link)
        self.addCleanup(os.unlink, link)
        real_realpath = os.path.realpath
        # the root path resolves, then is swapped for a symlink before it is opened without following
        with mock.patch.object(fsops.os.path, "realpath", return_value=link):
            doc = engine.run_materialize(self.engine_options(apply=True))
        self.assertIn("STAGING_FAILED", self.codes(doc, "error"))
        self.assertEqual(self.consumer.receipt()["state"], "complete")
        self.assertEqual(real_realpath(link), real_realpath(self.consumer.root))


def patch_pid():
    return mock.patch.object(engine.os, "getpid", return_value=FAKE_PID)


class DescriptorHygieneTests(RecoveryBase):
    """Every descriptor the installation holds is closed when it ends, success or failure."""

    @staticmethod
    def open_descriptors():
        return len(os.listdir("/dev/fd"))

    def test_no_descriptor_outlives_a_successful_installation(self):
        before = self.open_descriptors()
        doc = engine.run_materialize(self.engine_options(apply=True))
        self.assertEqual(doc["summary"]["exit_code"], 0, doc["findings"])
        self.assertEqual(self.open_descriptors(), before)

    def test_no_descriptor_outlives_a_failed_one(self):
        before = self.open_descriptors()
        doc = self.interrupt_after(1)
        self.assertIn("INSTALL_INTERRUPTED", self.codes(doc, "error"))
        self.assertEqual(self.open_descriptors(), before)

    def test_no_descriptor_outlives_a_failed_receipt_write(self):
        real_replace = os.replace

        def fail_for_the_receipt(src, dst, **kw):
            if dst == ".tooltempest.receipt.json":
                raise OSError(errno.EIO, "injected I/O error")
            return real_replace(src, dst, **kw)

        before = self.open_descriptors()
        with mock.patch.object(engine.os, "replace", side_effect=fail_for_the_receipt):
            doc = engine.run_materialize(self.engine_options(apply=True))
        self.assertIn("RECEIPT_WRITE_FAILED", self.codes(doc, "error"))
        self.assertEqual(self.open_descriptors(), before)
