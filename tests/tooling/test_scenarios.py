"""The problem this tool exists for: a clean checkout or a linked worktree whose
repository-local tooling is missing, found out before a hook runs.

Brain's actual pre-fix condition (Stage 0 finding F10): `.tooltempest.lock`
pins a commit and a hash for scripts/doc_sync.py, the pre-commit hook calls
scripts/doc_sync.py, and a clean worktree does not contain it, so the first
commit died in a raw Python error. These are disposable fixtures that
reproduce that condition; no real checkout is read or changed.
"""
import json
import os
import subprocess
import sys

from tooling_helpers import TOOLING_PY, ToolingCase, clean_env, run_tooling, sha256

HOOK = """#!/bin/sh
# stand-in for Brain's .githooks/pre-commit: it needs the vendored tool
exec python3 scripts/doc_sync.py pre-commit "$@"
"""


class BrainConditionTests(ToolingCase):
    def setUp(self):
        super().setUp()
        self.upstream.publish({"scripts/doc_sync.py": "print('doc_sync')\n",
                               "scripts/doc_sync_tier2.py": "print('tier2')\n",
                               "skills/spec/SKILL.md": "# spec\n"}, message="the pinned release")
        self.pin = self.upstream.fx.git("rev-parse", "HEAD")
        digest = sha256(self.upstream.files["scripts/doc_sync.py"])
        self.consumer.write_lock(self.pin, legacy=("scripts/doc_sync.py", digest))
        self.consumer.write(".githooks/pre-commit", HOOK, mode=0o755)

    def test_the_hook_fails_raw_but_verify_names_the_missing_dependency_first(self):
        raw = subprocess.run([self.consumer.path(".githooks/pre-commit")], cwd=self.consumer.root,
                             capture_output=True, env=clean_env({"HOME": self.home}))
        self.assertNotEqual(raw.returncode, 0)  # the condition itself: the hook cannot run
        self.assertIn(b"can't open file", raw.stderr)
        code, out, err = run_tooling(["verify", "--root", self.consumer.root, "--source", self.upstream.root],
                                     self.home)
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", out + err)
        self.assertIn("DEPENDENCY_MISSING", out)
        self.assertIn("scripts/doc_sync.py is required by the pin but is not installed", out)
        self.assertIn("materialize", out)
        self.assertIn("--apply", out)
        self.assertIn("local integrity      FAILED", out)
        self.assertEqual(err, "")

    def test_the_diagnosis_is_the_same_in_json_and_names_every_missing_file(self):
        code, doc = self.verify()
        self.assertEqual(code, 1)
        missing = sorted(f["path"] for f in doc["findings"] if f["code"] == "DEPENDENCY_MISSING")
        self.assertEqual(missing, ["scripts/doc_sync.py", "scripts/doc_sync_tier2.py"])
        record = self.file(doc, "scripts/doc_sync.py")
        self.assertEqual((record["local"]["status"], record["provenance"]), ("missing", "verified"))
        self.assertEqual(record["declared"]["by"], "lock")

    def test_the_diagnosis_does_not_need_the_source_at_all(self):
        code, doc = self.verify(source=os.path.join(self.home, "absent"))
        self.assertEqual(code, 1)
        self.assertEqual([f["path"] for f in doc["findings"] if f["code"] == "DEPENDENCY_MISSING"],
                         ["scripts/doc_sync.py"])  # the one file the lock declares
        self.assertIn("SOURCE_UNAVAILABLE", self.codes(doc, "error"))

    def test_verifying_changes_nothing_and_materialize_then_makes_the_hook_runnable(self):
        before = self.digest(self.consumer.root, self.upstream.root, self.home)
        self.verify()
        self.assertEqual(before, self.digest(self.consumer.root, self.upstream.root, self.home))
        self.apply()
        ran = subprocess.run([self.consumer.path(".githooks/pre-commit")], cwd=self.consumer.root,
                             capture_output=True, env=clean_env({"HOME": self.home}))
        self.assertEqual((ran.returncode, ran.stdout), (0, b"doc_sync\n"))
        self.assertEqual(self.verify()[0], 0)

    def test_a_tampered_dependency_is_not_mistaken_for_a_present_one(self):
        self.apply()
        self.consumer.write("scripts/doc_sync.py", "print('tampered')\n")
        code, doc = self.verify()
        self.assertEqual(code, 1)
        self.assertEqual(self.file(doc, "scripts/doc_sync.py")["local"]["status"], "modified")


class LinkedWorktreeTests(ToolingCase):
    """`.git` is a file pointing at a shared common directory, not a directory."""

    def setUp(self):
        super().setUp()
        self.publish_v1()
        self.consumer.write(".gitignore", "scripts/\nschemas/\n.tooltempest.receipt.json\n")
        self.consumer.fx.git("add", ".gitignore", ".tooltempest.lock")
        self.consumer.fx.commit("consumer")
        self.apply()  # the main checkout has its (ignored, untracked) tooling installed
        self.worktree = os.path.join(self.home, "linked-worktree")
        self.consumer.fx.git("worktree", "add", "-q", "-b", "wt", self.worktree)

    def test_the_worktree_really_has_a_git_file_and_lacks_the_dependencies(self):
        self.assertTrue(os.path.isfile(os.path.join(self.worktree, ".git")))
        self.assertFalse(os.path.exists(os.path.join(self.worktree, "scripts")))
        code, doc = self.verify(root=self.worktree)
        self.assertEqual(code, 1)
        self.assertEqual(sorted(f["path"] for f in doc["findings"] if f["code"] == "DEPENDENCY_MISSING"),
                         ["schemas/beta.schema.json", "scripts/alpha.py", "scripts/run.sh"])

    def test_materialize_acquires_the_dependencies_in_the_worktree_only(self):
        main_before = self.digest(self.consumer.path("scripts"), self.consumer.path("schemas"))
        main_receipt = self.consumer.read(".tooltempest.receipt.json")
        code, doc = self.materialize("--apply", root=self.worktree)
        self.assertEqual(code, 0, doc["findings"])
        for path, content in self.upstream.files.items():
            if path.startswith(("scripts/", "schemas/")):
                with open(os.path.join(self.worktree, path), "rb") as handle:
                    self.assertEqual(handle.read(), content, path)
        self.assertEqual(self.verify(root=self.worktree)[0], 0)
        self.assertTrue(os.path.exists(os.path.join(self.worktree, ".tooltempest.receipt.json")))
        # the main checkout was not touched, and no ~/.claude appeared
        worktree_git = self.consumer.fx.git("-C", self.worktree, "rev-parse", "--absolute-git-dir")
        self.assertIn("/worktrees/", worktree_git)
        self.assertTrue(os.path.exists(os.path.join(worktree_git, "tooltempest-install.lock")))
        main_git = self.consumer.fx.git("rev-parse", "--absolute-git-dir")
        self.assertNotEqual(worktree_git, main_git)
        self.assertEqual(self.digest(self.consumer.path("scripts"), self.consumer.path("schemas")), main_before)
        self.assertEqual(self.consumer.read(".tooltempest.receipt.json"), main_receipt)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude")))

    def test_it_works_from_inside_the_worktree_without_a_root_argument(self):
        code, out, err = run_tooling(["materialize", "--apply", "--source", self.upstream.root,
                                      "--format", "json"], self.home, cwd=self.worktree)
        doc = json.loads(out)
        self.assertEqual(code, 0, doc["findings"])
        self.assertEqual(os.path.realpath(doc["root"]), os.path.realpath(self.worktree))

    def test_a_worktree_and_its_main_checkout_can_install_at_the_same_time(self):
        procs = [subprocess.Popen([sys.executable, "-I", str(TOOLING_PY), "materialize", "--apply", "--root", root,
                                   "--source", self.upstream.root, "--format", "json"], stdout=subprocess.PIPE,
                                  env=clean_env({"HOME": self.home}))
                 for root in (self.consumer.root, self.worktree)]
        codes = [p.wait(timeout=300) for p in procs]
        for p in procs:
            p.stdout.close()
        self.assertEqual(codes, [0, 0])  # different checkouts have different locks
        self.assertEqual(self.verify(root=self.worktree)[0], 0)
        self.assertEqual(self.verify()[0], 0)


class FreshCloneTests(ToolingCase):
    def test_a_fresh_clone_lacks_the_untracked_tooling_and_acquires_it(self):
        self.publish_v1()
        self.consumer.write(".gitignore", "scripts/\nschemas/\n.tooltempest.receipt.json\n")
        self.consumer.fx.git("add", ".gitignore", ".tooltempest.lock")
        self.consumer.fx.commit("consumer")
        self.apply()
        clone = os.path.join(self.home, "fresh-clone")
        subprocess.run(["git", "clone", "-q", "--no-local", self.consumer.root, clone], env=clean_env(),
                       check=True, capture_output=True)
        self.assertFalse(os.path.exists(os.path.join(clone, "scripts")))
        code, doc = self.verify(root=clone)
        self.assertEqual(code, 1)
        self.assertEqual(doc["summary"]["local_integrity"], "failed")
        code, doc = self.materialize("--apply", root=clone)
        self.assertEqual(code, 0, doc["findings"])
        self.assertEqual(self.verify(root=clone)[0], 0)
        self.assertTrue(os.path.isdir(os.path.join(clone, ".git")))
