"""The platform preflight of `materialize --apply`: it runs after the plan and before
the lock file can be created, and when it fails the checkout is exactly as it was.

The state under test is a fresh checkout that has never been installed into, so the
install lock (`<git dir>/tooltempest-install.lock`) does not exist yet and its creation
would be visible. The guarantee tested is the checkout's: no lock file, staging file,
destination, directory or receipt. It is not a statement about the source cache, which
a `--allow-network` run may already have fetched into while planning.
"""
import hashlib
import json
import os
import stat
import subprocess
import sys
import textwrap
from unittest import mock

from tooling_helpers import (REPO_ROOT, RESULT_SCHEMA, ToolingCase, clean_env, engine, load_schema,
                             validate)
from tooling_core import fsops

DIRECT_RESULT = {"dry_run": False, "applied": False, "written": [], "receipt_written": False,
                 "post_verification": None}


class PlatformPreflightTests(ToolingCase):
    def setUp(self):
        super().setUp()
        self.publish_v1()
        git_dir = engine.run_git(["rev-parse", "--absolute-git-dir"], cwd=self.consumer.root).stdout
        self.lock_file = os.path.join(git_dir.decode().strip(), "tooltempest-install.lock")

    def tree_state(self):
        """Everything under the checkout, the git directory included: type, mode, size, content hash."""
        state = {}
        for base, dirs, files in os.walk(self.consumer.root):
            dirs.sort()
            for name in dirs + files:
                path = os.path.join(base, name)
                info = os.lstat(path)
                digest = None
                if stat.S_ISREG(info.st_mode):
                    with open(path, "rb") as handle:
                        digest = hashlib.sha256(handle.read()).hexdigest()
                state[os.path.relpath(path, self.consumer.root)] = (
                    stat.S_IFMT(info.st_mode), stat.S_IMODE(info.st_mode), info.st_size, digest)
        return state

    def traced_run(self, **options):
        """Run materialize in-process and record the order of the preflight and the lock."""
        order = []
        real_support, real_lock = fsops.require_support, engine._acquire_lock

        def support():
            order.append("require_support")
            return real_support()

        def lock(opts):
            order.append("acquire_lock")
            return real_lock(opts)

        with mock.patch.object(fsops, "require_support", side_effect=support), \
                mock.patch.object(engine, "_acquire_lock", side_effect=lock):
            doc = engine.run_materialize(self.engine_options(**options))
        return doc, order

    def assertUnsupportedPlatformResult(self, doc):
        self.assertEqual(doc["command"], "materialize")
        self.assertEqual(doc["summary"]["exit_code"], 1)
        self.assertEqual(doc["summary"]["errors"], 1)
        self.assertIsNone(doc["error"], "a refusal is a finding, not a tool failure")
        findings = [f for f in doc["findings"] if f["severity"] == "error"]
        self.assertEqual([f["code"] for f in findings], ["UNSUPPORTED_PLATFORM"])
        self.assertIn("lacks", findings[0]["message"])
        self.assertIn("no lock file, staging file, destination or receipt was created", findings[0]["message"])
        self.assertEqual(doc["result"], DIRECT_RESULT)
        self.assertEqual(validate(doc, load_schema(RESULT_SCHEMA)), [])

    def test_an_unsupported_platform_stops_before_the_lock_file_is_created(self):
        before = self.tree_state()
        self.assertFalse(os.path.lexists(self.lock_file), "the fixture must start without a lock file")
        with mock.patch.object(fsops.os, "supports_dir_fd", set()):
            doc, order = self.traced_run(apply=True)
        self.assertFalse(os.path.lexists(self.lock_file), "the failed preflight left a lock file behind")
        self.assertEqual(self.tree_state(), before)
        self.assertEqual(order, ["require_support"], "the lock must not even be attempted")
        for rel in ("scripts/alpha.py", "scripts/run.sh", "schemas/beta.schema.json", ".tooltempest.receipt.json"):
            self.assertFalse(self.consumer.exists(rel), rel)
        self.assertEqual([n for _, _, names in os.walk(self.consumer.root) for n in names
                          if n.startswith(".tooltempest-stage")], [])
        self.assertUnsupportedPlatformResult(doc)

    def test_each_missing_primitive_is_caught_by_the_same_preflight(self):
        for func in (os.open, os.mkdir, os.unlink, os.rmdir, os.stat, os.rename):
            with self.subTest(func.__name__):
                before = self.tree_state()
                with mock.patch.object(fsops.os, "supports_dir_fd", set(os.supports_dir_fd) - {func}):
                    doc, order = self.traced_run(apply=True)
                self.assertEqual(order, ["require_support"])
                self.assertIn("lacks %s, which" % func.__name__, doc["findings"][-1]["message"])
                self.assertFalse(os.path.lexists(self.lock_file))
                self.assertEqual(self.tree_state(), before)

    def test_on_a_supported_platform_the_preflight_comes_first_and_the_lock_is_then_created(self):
        self.assertFalse(os.path.lexists(self.lock_file))
        doc, order = self.traced_run(apply=True)
        self.assertEqual(doc["summary"]["exit_code"], 0, doc["findings"])
        self.assertEqual(order, ["require_support", "acquire_lock"])
        self.assertTrue(os.path.lexists(self.lock_file), "control: this test would see a lock file")
        self.assertTrue(self.consumer.exists("scripts/alpha.py"))

    def test_a_dry_run_and_a_run_with_nothing_to_write_do_not_need_the_primitives(self):
        with mock.patch.object(fsops.os, "supports_dir_fd", set()):
            dry, order = self.traced_run(apply=False)
        self.assertEqual((dry["summary"]["exit_code"], order), (0, []))
        self.assertFalse(os.path.lexists(self.lock_file))
        self.apply()  # installs; the lock file now exists
        before = self.tree_state()
        with mock.patch.object(fsops.os, "supports_dir_fd", set()):
            again, order = self.traced_run(apply=True)
        self.assertEqual((again["summary"]["exit_code"], order), (0, []))
        self.assertEqual(self.tree_state(), before)

    def test_the_command_line_reports_the_same_contract(self):
        script = textwrap.dedent('''
            import os, sys
            sys.dont_write_bytecode = True
            sys.path.insert(0, %(scripts)r)
            os.supports_dir_fd = set()
            from tooling_core.cli import main
            sys.exit(main(sys.argv[1:]))
        ''' % {"scripts": str(REPO_ROOT / "scripts")})
        before = self.tree_state()
        proc = subprocess.run([sys.executable, "-I", "-c", script, *self.args("materialize", "--apply")],
                              capture_output=True, env=clean_env({"HOME": self.home}))
        self.assertEqual(proc.returncode, 1, proc.stderr.decode()[:300])
        doc = json.loads(proc.stdout.decode())
        self.assertEqual(doc["summary"]["exit_code"], proc.returncode)
        self.assertUnsupportedPlatformResult(doc)
        self.assertFalse(os.path.lexists(self.lock_file))
        self.assertEqual(self.tree_state(), before)
