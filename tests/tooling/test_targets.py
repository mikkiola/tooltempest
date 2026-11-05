"""The two targets of the materializer, receipt version 2, the install lock outside Git, and test isolation.

ADR-0017 decides that the consumer target (core and the contract root) is the default and the client
target (skills/, rules/) is selected only by an explicit argument with an explicit root; that the receipt
and the installing journal get one additive new schema version, written only when needed and read
together with the old one; and that a root outside Git keeps its install lock inside itself, never
through a link. Everything here runs in throwaway directories with a throwaway HOME; the client root is
always a throwaway directory.
"""
import errno
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

from tooling_helpers import (REAL_CLAUDE_DIR, REAL_HOME, TOOLING_PY, RealClaudeTouched, ToolingCase,
                             UNREACHABLE_URL, engine, load_schema, run_tooling, RECEIPT_SCHEMA, validate)
from tooling_core.declarations import FormatError, parse_receipt
from tooling_core.objects import GitFailure, GitMissing

CONTRACT = "docs/tooltempest/consumer-contract.md"
CONTRACT_FILES = {CONTRACT: "# contract v1\n", "skills/demo/SKILL.md": "# skill v1\n", "rules/r.md": "# rule v1\n"}


class TargetCase(ToolingCase):
    def setUp(self):
        super().setUp()
        self.client = tempfile.mkdtemp(prefix="client-root-")
        self.addCleanup(shutil.rmtree, self.client, ignore_errors=True)

    def client_lock(self, commit):
        with open(os.path.join(self.client, ".tooltempest.lock"), "w", encoding="utf-8") as handle:
            json.dump({"repository": UNREACHABLE_URL, "commit": commit}, handle)
            handle.write("\n")

    def client_path(self, rel):
        return os.path.join(self.client, rel)

    def client_receipt(self):
        with open(self.client_path(".tooltempest.receipt.json"), "rb") as handle:
            return json.loads(handle.read())

    def client_run(self, command, *extra, root=None):
        return self.run_json(command, "--target", "client", *extra, root=root or self.client)

    def publish_with_contract(self):
        commit = self.publish_v1(files=CONTRACT_FILES)
        self.client_lock(commit)
        return commit

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


class ConsumerTargetTests(TargetCase):
    def test_the_consumer_target_installs_core_and_the_contract_and_nothing_of_the_client_or_the_reference_docs(self):
        self.publish_with_contract()
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 0, doc["findings"])
        self.assertEqual(sorted(doc["result"]["written"]),
                         [CONTRACT, "schemas/beta.schema.json", "scripts/alpha.py", "scripts/run.sh"])
        self.assertEqual(self.consumer.read(CONTRACT), b"# contract v1\n")
        for rel in ("skills", "rules", "docs/reference"):
            self.assertFalse(self.consumer.exists(rel), rel)
        skipped = {item["path"]: item["reason"] for item in doc["skipped"]}
        self.assertEqual(sorted(skipped), ["docs/reference/rules.md", "rules/r.md", "skills/demo/SKILL.md"])
        self.assertIn("--target client", skipped["skills/demo/SKILL.md"])
        self.assertIn("reference", skipped["docs/reference/rules.md"])
        self.assertEqual(self.verify()[0], 0)

    def test_a_contract_file_in_the_way_with_other_content_is_refused_not_overwritten(self):
        self.publish_with_contract()
        self.consumer.write(CONTRACT, "# the consumer's own text\n")
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 1)
        self.assertIn("CONFLICT_UNMANAGED", self.codes(doc, "error"))
        self.assertEqual(self.consumer.read(CONTRACT), b"# the consumer's own text\n")
        self.assertFalse(self.consumer.exists("scripts/alpha.py"), "a refusal writes nothing at all")

    def test_the_contract_file_is_not_ignored_by_the_usual_ignore_rules_and_no_ignore_file_is_touched(self):
        self.publish_with_contract()
        ignore = "scripts/\nschemas/\n.tooltempest.receipt.json\n"
        self.consumer.write(".gitignore", ignore)
        self.consumer.fx.git("add", ".gitignore", ".tooltempest.lock")
        self.consumer.fx.commit("consumer")
        self.apply()
        check = subprocess.run(["git", "check-ignore", "-q", CONTRACT], cwd=self.consumer.root,
                               capture_output=True, env=dict(os.environ, HOME=self.home))
        self.assertEqual(check.returncode, 1, "the contract file is ignored")
        status = self.consumer.fx.git("status", "--porcelain", "--untracked-files=all")
        self.assertIn("?? " + CONTRACT, status.splitlines())
        self.assertEqual(self.consumer.read(".gitignore"), ignore.encode(), "the tool must not edit .gitignore")
        self.consumer.fx.git("add", CONTRACT)
        self.assertIn(CONTRACT, self.consumer.fx.git("ls-files").splitlines())

    def test_no_consumer_run_writes_anything_under_home(self):
        self.publish_with_contract()
        before = self.digest(self.home)
        self.materialize()
        self.apply()
        self.verify()
        self.run_json("status")
        self.assertEqual(self.digest(self.home), before)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude")))


class ClientTargetTests(TargetCase):
    def test_the_client_target_installs_only_the_client_roots_into_the_root_it_is_given(self):
        self.publish_with_contract()
        code, doc = self.client_run("materialize", "--apply")
        self.assertEqual(code, 0, doc["findings"])
        self.assertEqual(sorted(doc["result"]["written"]), ["rules/r.md", "skills/demo/SKILL.md"])
        with open(self.client_path("skills/demo/SKILL.md"), "rb") as handle:
            self.assertEqual(handle.read(), b"# skill v1\n")
        for rel in ("scripts", "schemas", "docs"):
            self.assertFalse(os.path.exists(self.client_path(rel)), rel)
        skipped = {item["path"] for item in doc["skipped"]}
        self.assertEqual(skipped, {"docs/reference/rules.md", CONTRACT, "schemas/beta.schema.json",
                                   "scripts/alpha.py", "scripts/run.sh"})
        self.assertFalse(self.consumer.exists("skills"), "the consumer was not the root")
        self.assertEqual(self.client_run("verify")[0], 0)

    def test_the_lock_and_the_receipt_of_the_client_target_live_in_the_client_root(self):
        self.publish_with_contract()
        self.client_run("materialize", "--apply")
        receipt = self.client_receipt()
        self.assertEqual((receipt["state"], receipt["schema_version"]), ("complete", 2))
        self.assertEqual(sorted(f["path"] for f in receipt["files"]), ["rules/r.md", "skills/demo/SKILL.md"])
        self.assertEqual(validate(receipt, load_schema(RECEIPT_SCHEMA)), [])
        self.assertFalse(self.consumer.exists(".tooltempest.receipt.json"))

    def test_a_second_apply_of_the_client_target_changes_nothing(self):
        self.publish_with_contract()
        self.client_run("materialize", "--apply")
        before = self.digest(self.client)
        code, doc = self.client_run("materialize", "--apply")
        self.assertEqual((code, doc["result"]["written"], doc["result"]["receipt_written"]), (0, [], False))
        self.assertEqual(self.digest(self.client), before)

    def test_a_dry_run_of_the_client_target_writes_nothing(self):
        self.publish_with_contract()
        before = self.digest(self.client, self.home, self.consumer.root)
        code, doc = self.client_run("materialize")
        self.assertEqual(code, 0, doc["findings"])
        self.assertTrue(doc["result"]["dry_run"])
        self.assertEqual(self.digest(self.client, self.home, self.consumer.root), before)

    def test_an_unmanaged_or_edited_client_file_is_refused_and_kept(self):
        self.publish_with_contract()
        os.makedirs(self.client_path("skills/demo"))
        with open(self.client_path("skills/demo/SKILL.md"), "w") as handle:
            handle.write("# the user's own skill\n")
        code, doc = self.client_run("materialize", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("CONFLICT_UNMANAGED", self.codes(doc, "error"))
        with open(self.client_path("skills/demo/SKILL.md")) as handle:
            self.assertEqual(handle.read(), "# the user's own skill\n")
        self.assertFalse(os.path.exists(self.client_path("rules")), "a refusal writes nothing at all")

    def test_a_file_the_tool_does_not_manage_in_the_client_root_is_never_touched(self):
        self.publish_with_contract()
        with open(self.client_path("settings.json"), "w") as handle:
            handle.write('{"mine": true}\n')
        os.makedirs(self.client_path("skills/other"))
        with open(self.client_path("skills/other/SKILL.md"), "w") as handle:
            handle.write("# another skill\n")
        self.assertEqual(self.client_run("materialize", "--apply")[0], 0)
        with open(self.client_path("settings.json")) as handle:
            self.assertEqual(handle.read(), '{"mine": true}\n')
        with open(self.client_path("skills/other/SKILL.md")) as handle:
            self.assertEqual(handle.read(), "# another skill\n")

    def test_there_is_no_default_client_directory(self):
        self.publish_with_contract()
        for command in ("verify", "status", "materialize"):
            with self.subTest(command):
                before = self.digest(self.home, self.consumer.root)
                code, out, err = run_tooling([command, "--target", "client", "--source", self.upstream.root,
                                              "--format", "json"], self.home, cwd=self.consumer.root)
                doc = json.loads(out)
                self.assertEqual((code, doc["error"]["code"]), (2, "USAGE"), out)
                self.assertIn("--root", doc["error"]["message"])
                self.assertEqual(self.digest(self.home, self.consumer.root), before)
                self.assertFalse(self.consumer.exists("skills"))

    def test_the_client_target_is_never_selected_by_default(self):
        self.publish_with_contract()
        code, out, err = run_tooling(["materialize", "--apply", "--source", self.upstream.root, "--format", "json"],
                                     self.home, cwd=self.consumer.root)
        self.assertEqual(code, 0, out)
        self.assertFalse(self.consumer.exists("skills"))
        self.assertFalse(self.consumer.exists("rules"))
        self.assertEqual(os.listdir(self.home), [])


class ReceiptVersionTests(TargetCase):
    def test_a_run_that_needs_no_new_root_writes_version_1_and_nothing_else_changed_in_the_format(self):
        self.publish_v1()
        self.apply()
        receipt = self.consumer.receipt()
        self.assertEqual(receipt["schema_version"], 1)
        self.assertEqual(sorted(receipt), ["commit", "files", "repository", "schema_version", "state", "tool",
                                           "tool_version"])
        for entry in receipt["files"]:
            self.assertEqual(sorted(entry), ["git_blob", "mode", "path", "sha256"])
        self.assertReceiptValid()

    def test_a_run_that_installs_the_contract_writes_version_2(self):
        self.publish_with_contract()
        self.apply()
        self.assertEqual(self.consumer.receipt()["schema_version"], 2)
        self.assertIn(CONTRACT, [f["path"] for f in self.consumer.receipt()["files"]])
        self.assertReceiptValid()

    def test_a_version_1_receipt_of_the_earlier_tool_is_not_rewritten_when_nothing_changes(self):
        self.publish_v1()
        self.apply()
        earlier = self.consumer.read(".tooltempest.receipt.json").replace(b'"tool_version": "0.2.0"',
                                                                         b'"tool_version": "0.1.0"')
        self.assertIn(b'"tool_version": "0.1.0"', earlier)
        self.consumer.write(".tooltempest.receipt.json", earlier)
        code, doc = self.materialize("--apply")
        self.assertEqual((code, doc["result"]["written"], doc["result"]["receipt_written"]), (0, [], False))
        self.assertEqual(self.consumer.read(".tooltempest.receipt.json"), earlier)
        self.assertEqual(self.verify()[0], 0)

    def test_a_version_1_receipt_round_trips_byte_for_byte_with_its_own_writer_recorded(self):
        self.publish_v1()
        self.apply()
        raw = self.consumer.read(".tooltempest.receipt.json").replace(b'"tool_version": "0.2.0"',
                                                                     b'"tool_version": "0.1.0"')
        from tooling_core.declarations import serialize_receipt
        receipt = parse_receipt(raw)
        self.assertEqual((receipt.schema_version, receipt.tool_version), (1, "0.1.0"))
        self.assertEqual(serialize_receipt(receipt, tool_version=receipt.tool_version), raw)

    def test_a_version_1_install_is_upgraded_to_version_2_when_the_pin_brings_the_contract(self):
        self.publish_v1()
        self.apply()
        self.assertEqual(self.consumer.receipt()["schema_version"], 1)
        commit2 = self.upstream.publish({CONTRACT: "# contract v1\n"}, message="the contract arrives")
        self.consumer.write_lock(commit2)
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 0, doc["findings"])
        receipt = self.consumer.receipt()
        self.assertEqual((receipt["schema_version"], receipt["tool_version"]), (2, "0.2.0"))
        self.assertEqual(self.verify()[0], 0)

    def test_an_older_tool_refuses_a_version_2_receipt_instead_of_misreading_it(self):
        self.publish_with_contract()
        self.apply()
        raw = self.consumer.read(".tooltempest.receipt.json")
        parse_receipt(raw)  # this tool reads it
        with self.assertRaises(FormatError) as ctx:
            parse_receipt(raw, supported=(1,))  # the version gate the earlier tool had
        self.assertEqual(ctx.exception.code, "RECEIPT_MALFORMED")
        self.assertIn("schema_version", ctx.exception.message)


class JournalVersionTests(TargetCase):
    def test_the_journal_of_an_installation_that_needs_no_new_root_is_version_1(self):
        self.publish_v1()
        self.apply()
        commit2 = self.upstream.publish({"scripts/alpha.py": "print('alpha v2')\n",
                                         "schemas/gamma.schema.json": '{"v": 2}\n'}, message="v2")
        self.consumer.write_lock(commit2)
        self.interrupt_after(1)
        journal = self.consumer.receipt()
        self.assertEqual((journal["state"], journal["schema_version"]), ("installing", 1))
        self.assertReceiptValid()

    def test_the_journal_that_includes_a_new_root_is_version_2_and_recovery_finishes_it(self):
        self.publish_v1()
        self.apply()
        commit2 = self.upstream.publish({CONTRACT: "# contract v1\n", "scripts/alpha.py": "print('alpha v2')\n"},
                                        message="the contract arrives")
        self.consumer.write_lock(commit2)
        doc = self.interrupt_after(1)
        self.assertIn("INSTALL_INTERRUPTED", self.codes(doc, "error"))
        journal = self.consumer.receipt()
        self.assertEqual((journal["state"], journal["schema_version"]), ("installing", 2))
        self.assertReceiptValid()
        previous = {f["path"]: f["previous_sha256"] for f in journal["files"]}
        self.assertIsNone(previous[CONTRACT])  # did not exist before
        code, status = self.run_json("status")
        self.assertIn("RECOVERY_REQUIRED", self.codes(status, "error"))
        self.assertNotEqual(self.verify()[0], 0)
        # the journal is read by recovery and finished by a repeated apply
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 0, doc["findings"])
        receipt = self.consumer.receipt()
        self.assertEqual((receipt["state"], receipt["schema_version"]), ("complete", 2))
        self.assertEqual(self.consumer.read(CONTRACT), b"# contract v1\n")
        self.assertEqual(self.verify()[0], 0)

    def test_an_older_tool_refuses_the_version_2_journal(self):
        self.publish_v1()
        self.apply()
        commit2 = self.upstream.publish({CONTRACT: "# contract v1\n", "scripts/alpha.py": "print('alpha v2')\n"},
                                        message="the contract arrives")
        self.consumer.write_lock(commit2)
        self.interrupt_after(1)
        raw = self.consumer.read(".tooltempest.receipt.json")
        parse_receipt(raw)
        with self.assertRaises(FormatError) as ctx:
            parse_receipt(raw, supported=(1,))
        self.assertIn("schema_version", ctx.exception.message)

    def test_the_client_journal_is_version_2_and_recovery_finishes_it(self):
        commit = self.publish_with_contract()
        real_replace = os.replace
        renames = []

        def failing(src, dst, **kw):
            if not dst.endswith(".tooltempest.receipt.json"):
                if len(renames) == 1:
                    raise OSError(errno.EIO, "injected I/O error")
                renames.append(dst)
            return real_replace(src, dst, **kw)

        options = self.engine_options(root=self.client, lock_path=self.client_path(".tooltempest.lock"),
                                      receipt_path=self.client_path(".tooltempest.receipt.json"),
                                      apply=True, target="client")
        with mock.patch.object(engine.os, "replace", side_effect=failing):
            doc = engine.run_materialize(options)
        self.assertIn("INSTALL_INTERRUPTED", self.codes(doc, "error"))
        journal = self.client_receipt()
        self.assertEqual((journal["state"], journal["schema_version"], journal["commit"]), ("installing", 2, commit))
        self.assertEqual(validate(journal, load_schema(RECEIPT_SCHEMA)), [])
        code, doc = self.client_run("materialize", "--apply")
        self.assertEqual(code, 0, doc["findings"])
        self.assertEqual(self.client_receipt()["state"], "complete")
        self.assertEqual(self.client_run("verify")[0], 0)


class InstallLockOutsideGitTests(TargetCase):
    def lock_path(self):
        return self.client_path(".tooltempest-install.lock")

    def test_the_lock_is_created_inside_a_root_outside_git_and_a_second_run_with_it_present_succeeds(self):
        commit = self.publish_with_contract()
        self.assertEqual(self.client_run("materialize", "--apply")[0], 0)
        info = os.lstat(self.lock_path())
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode) & 0o077, 0, "the lock is private to its owner")
        commit2 = self.upstream.publish({"skills/demo/SKILL.md": "# skill v2\n"}, message="v2")
        self.client_lock(commit2)
        code, doc = self.client_run("materialize", "--apply")  # the lock file already exists
        self.assertEqual(code, 0, doc["findings"])
        self.assertEqual(doc["result"]["written"], ["skills/demo/SKILL.md"])
        self.assertTrue(stat.S_ISREG(os.lstat(self.lock_path()).st_mode))

    def test_a_dry_run_and_a_run_with_nothing_to_write_do_not_create_the_lock(self):
        self.publish_with_contract()
        self.client_run("materialize")
        self.assertFalse(os.path.lexists(self.lock_path()))
        self.client_run("materialize", "--apply")
        os.unlink(self.lock_path())
        self.client_run("materialize", "--apply")  # nothing to do
        self.assertFalse(os.path.lexists(self.lock_path()))

    def test_a_lock_held_by_another_process_makes_the_run_busy_and_changes_nothing(self):
        self.publish_with_contract()
        holder = subprocess.Popen(
            [sys.executable, "-c", textwrap.dedent('''
                import fcntl, os, sys
                fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX)
                print("locked", flush=True)
                sys.stdin.read()
            '''), self.lock_path()], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            self.assertEqual(holder.stdout.readline().strip(), b"locked")
            code, doc = self.client_run("materialize", "--apply")
            self.assertEqual(code, 1)
            self.assertIn("INSTALL_LOCKED", self.codes(doc, "error"))
            self.assertEqual(doc["result"]["written"], [])
            self.assertFalse(os.path.exists(self.client_path("skills")))
        finally:
            holder.stdin.close()
            holder.wait(timeout=30)
            holder.stdout.close()
        self.assertEqual(self.client_run("materialize", "--apply")[0], 0)

    def assert_refused_before_anything_is_written(self, target_to_watch=None):
        code, out, err = run_tooling(["materialize", "--apply", "--target", "client", "--root", self.client,
                                      "--source", self.upstream.root, "--format", "json"], self.home)
        doc = json.loads(out)
        self.assertEqual((code, doc["error"]["code"]), (2, "INSTALL_LOCK_UNSAFE"), out)
        self.assertFalse(os.path.exists(self.client_path("skills")), "nothing is written")
        self.assertFalse(os.path.exists(self.client_path(".tooltempest.receipt.json")))
        if target_to_watch is not None:
            self.assertFalse(os.path.lexists(target_to_watch), "the link target must not be created")

    def test_a_symlink_at_the_lock_name_is_refused_and_its_target_is_not_created(self):
        self.publish_with_contract()
        elsewhere = os.path.join(self.client, "elsewhere")
        os.symlink(elsewhere, self.lock_path())  # a dangling link: following it would create the target
        self.assert_refused_before_anything_is_written(elsewhere)
        self.assertTrue(os.path.islink(self.lock_path()))

    def test_a_symlink_to_an_existing_file_is_refused_and_the_file_is_not_locked_or_changed(self):
        self.publish_with_contract()
        victim = os.path.join(self.home, "victim.txt")
        with open(victim, "w") as handle:
            handle.write("not a lock\n")
        os.symlink(victim, self.lock_path())
        self.assert_refused_before_anything_is_written()
        with open(victim) as handle:
            self.assertEqual(handle.read(), "not a lock\n")

    def test_a_directory_at_the_lock_name_is_refused(self):
        self.publish_with_contract()
        os.mkdir(self.lock_path())
        self.assert_refused_before_anything_is_written()
        self.assertTrue(os.path.isdir(self.lock_path()))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs mkfifo")
    def test_another_non_regular_entry_at_the_lock_name_is_refused(self):
        self.publish_with_contract()
        os.mkfifo(self.lock_path())
        self.assert_refused_before_anything_is_written()

    def test_a_root_that_is_a_git_checkout_still_keeps_its_lock_in_its_git_directory(self):
        self.publish_v1()
        self.apply()
        self.upstream.publish({"scripts/alpha.py": "print('alpha v2')\n"}, message="v2")
        self.consumer.write_lock(self.upstream.fx.git("rev-parse", "HEAD"))
        self.apply()
        self.assertFalse(self.consumer.exists(".tooltempest-install.lock"))
        git_dir = self.consumer.fx.git("rev-parse", "--absolute-git-dir")
        self.assertTrue(os.path.isfile(os.path.join(git_dir, "tooltempest-install.lock")))


class InstallLockPlacementTests(TargetCase):
    """Where the lock goes follows what Git says about the root, and never moves silently."""

    def test_a_root_inside_another_repositorys_work_tree_never_gets_its_lock_in_that_repositorys_git_dir(self):
        commit = self.publish_with_contract()
        self.client = os.path.join(self.consumer.root, "inner-client")  # in the consumer's work tree, not its top
        os.mkdir(self.client)
        self.client_lock(commit)
        code, doc = self.client_run("materialize", "--apply")
        self.assertEqual(code, 0, doc["findings"])
        self.assertTrue(stat.S_ISREG(os.lstat(self.client_path(".tooltempest-install.lock")).st_mode))
        git_dir = self.consumer.fx.git("rev-parse", "--absolute-git-dir")
        self.assertFalse(os.path.lexists(os.path.join(git_dir, "tooltempest-install.lock")),
                         "a lock inside another repository's Git directory")

    def test_a_root_typed_in_another_letter_case_still_keeps_its_lock_in_its_git_directory(self):
        root = self.consumer.root
        swapped = os.path.join(os.path.dirname(root), os.path.basename(root).swapcase())
        if not os.path.exists(swapped):
            self.skipTest("a case-sensitive filesystem: the same directory has no second spelling")
        self.publish_v1()
        code, doc = self.materialize("--apply", root=swapped)
        self.assertEqual(code, 0, doc["findings"])
        git_dir = self.consumer.fx.git("rev-parse", "--absolute-git-dir")
        self.assertTrue(os.path.isfile(os.path.join(git_dir, "tooltempest-install.lock")))
        self.assertFalse(self.consumer.exists(".tooltempest-install.lock"))

    def test_when_git_cannot_say_where_the_lock_belongs_the_run_stops_before_any_write(self):
        self.publish_with_contract()
        failures = {"dubious ownership": GitFailure("git rev-parse failed (exit 128): fatal: detected "
                                                    "dubious ownership in repository at '/x'"),
                    "git missing": GitMissing("git executable not found")}
        for name, failure in failures.items():
            with self.subTest(name):
                before = self.digest(self.client)
                options = self.engine_options(root=self.client, lock_path=self.client_path(".tooltempest.lock"),
                                              receipt_path=self.client_path(".tooltempest.receipt.json"),
                                              target="client", apply=True)
                with mock.patch.object(engine, "run_git", side_effect=failure):
                    with self.assertRaises(engine.ToolFailure) as caught:
                        engine.run_materialize(options)
                self.assertEqual(caught.exception.code, "GIT_FAILED")
                self.assertEqual(self.digest(self.client), before)
                self.assertFalse(os.path.lexists(self.client_path(".tooltempest-install.lock")))
                self.assertFalse(os.path.exists(self.client_path("skills")))


class SharedReceiptTests(TargetCase):
    """One target never deletes the records of the other target from a receipt it finds."""

    def test_a_client_run_in_a_root_that_holds_consumer_records_is_refused_and_the_receipt_is_kept(self):
        self.publish_with_contract()
        self.apply()
        kept = self.consumer.receipt()
        for extra in ((), ("--apply",)):
            with self.subTest(extra=extra):
                code, doc = self.client_run("materialize", *extra, root=self.consumer.root)
                self.assertEqual(code, 1)
                self.assertIn("RECEIPT_OTHER_TARGET", self.codes(doc, "error"))
                self.assertEqual(self.consumer.receipt(), kept)
                self.assertFalse(self.consumer.exists("skills"))

    def test_a_journal_that_records_consumer_files_is_refused_by_the_client_target_too(self):
        self.publish_with_contract()
        self.interrupt_after(1)
        self.assertEqual(self.consumer.receipt()["state"], "installing")
        kept = self.consumer.receipt()
        code, doc = self.client_run("materialize", "--apply", root=self.consumer.root)
        self.assertEqual(code, 1)
        self.assertIn("RECEIPT_OTHER_TARGET", self.codes(doc, "error"))
        self.assertEqual(self.consumer.receipt(), kept)
        self.assertFalse(self.consumer.exists("skills"))

    def test_a_consumer_run_in_a_root_that_holds_client_records_is_refused_and_the_receipt_is_kept(self):
        self.publish_with_contract()
        self.client_run("materialize", "--apply")
        kept = self.client_receipt()
        for extra in ((), ("--apply",)):
            with self.subTest(extra=extra):
                code, doc = self.run_json("materialize", *extra, root=self.client)
                self.assertEqual(code, 1)
                self.assertIn("RECEIPT_OTHER_TARGET", self.codes(doc, "error"))
                self.assertEqual(self.client_receipt(), kept)
                self.assertFalse(os.path.exists(self.client_path("scripts")))


class TestIsolationGuardTests(unittest.TestCase):
    """No test may touch the real ~/.claude: a guard in the helpers stops it, and this shows it fires."""

    def test_a_write_under_the_real_claude_directory_is_stopped_before_it_happens(self):
        probe = os.path.join(REAL_CLAUDE_DIR, "tooltempest-guard-probe-%d" % os.getpid())
        with self.assertRaises(RealClaudeTouched):
            os.mkdir(probe)
        with self.assertRaises(RealClaudeTouched):
            open(os.path.join(probe, "x"), "w")
        with self.assertRaises(RealClaudeTouched):
            os.rename(os.path.join(tempfile.gettempdir(), "nonexistent-guard-source"),
                      os.path.join(REAL_CLAUDE_DIR, "moved"))
        self.assertFalse(os.path.lexists(probe), "the guard fires before the call, so nothing was created")

    def test_reading_the_real_claude_directory_is_not_stopped(self):
        os.path.exists(REAL_CLAUDE_DIR)  # no exception

    def test_a_throwaway_directory_is_not_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "x"), "w") as handle:
                handle.write("ok")

    def test_the_tool_is_never_started_with_the_real_home(self):
        with self.assertRaises(RealClaudeTouched):
            run_tooling(["--version"], REAL_HOME)


if __name__ == "__main__":
    unittest.main()
