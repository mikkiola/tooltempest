"""What `tooling.py` materializes is exactly what MANIFEST.txt ships, for the candidate commit.

The source is this checkout's own Git objects at its exact HEAD (the candidate
commit); nothing is fetched and the checkout is only read. The destination is
a disposable consumer repository under the system temp directory, with HOME
pointed at a temporary directory. The tests check the complete expected file set, the content
hash, the Git blob id and the file mode of every file, the receipt, that the
skipped roots stay out, and that the materialized copy actually runs: an
incomplete manifest shows up as a module that cannot be imported there.
"""
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import unittest

from dist_helpers import (COMPOSITION_DIRS, REPO_ROOT, TempDirCase, candidate_commit, clean_env,
                          git_out, run_python, validate)

INSTALL_ROOTS = ("scripts/", "schemas/")
RESULT_SCHEMA = REPO_ROOT / "schemas" / "tooling-result.schema.json"
RECEIPT_SCHEMA = REPO_ROOT / "schemas" / "tooling-receipt.schema.json"
TOOLING = REPO_ROOT / "scripts" / "tooling.py"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


class Candidate:
    """The facts about the candidate commit, read from Git, not from the working tree."""

    def __init__(self):
        self.commit = candidate_commit()
        self.manifest = [line for line in git_out("show", "%s:MANIFEST.txt" % self.commit).decode().splitlines()
                         if line.strip()]
        self.installed = sorted(p for p in self.manifest if p.startswith(INSTALL_ROOTS))
        self.skipped = sorted(p for p in self.manifest if not p.startswith(INSTALL_ROOTS))
        self.blobs = {}
        for line in git_out("ls-tree", "-r", "-z", self.commit).decode("utf-8", "surrogateescape").split("\0"):
            if not line:
                continue
            meta, path = line.split("\t", 1)
            mode, kind, oid = meta.split()
            self.blobs[path] = (mode, oid)

    def bytes_of(self, path):
        return git_out("cat-file", "blob", self.blobs[path][1])


CANDIDATE = Candidate()


class MaterializedCase(TempDirCase):
    """Materialize the candidate into a fresh consumer once per test."""

    def setUp(self):
        self.home = self.tempdir()
        self.consumer = self.tempdir() / "consumer"
        self.consumer.mkdir()
        subprocess.run(["git", "init", "-q", str(self.consumer)], check=True, env=clean_env())
        (self.consumer / ".tooltempest.lock").write_text(json.dumps({
            "repository": "https://tooltempest.invalid/tooltempest.git", "commit": CANDIDATE.commit}, indent=2) + "\n")

    def tooling(self, *args, script=TOOLING):
        proc = subprocess.run([sys.executable, "-I", str(script), *args, "--root", str(self.consumer),
                               "--source", str(REPO_ROOT), "--format", "json"],
                              capture_output=True, env=clean_env({"HOME": str(self.home)}), check=False,
                              stdin=subprocess.DEVNULL)
        doc = json.loads(proc.stdout.decode("utf-8"))
        self.assertEqual(proc.returncode, doc["summary"]["exit_code"])
        self.assertEqual(validate(doc, load(RESULT_SCHEMA)), [])
        return proc.returncode, doc

    def materialize(self):
        code, doc = self.tooling("materialize", "--apply")
        self.assertEqual(code, 0, doc["findings"])
        return doc

    def installed_on_disk(self):
        found = []
        for base, dirs, files in os.walk(self.consumer):
            dirs[:] = sorted(d for d in dirs if d != ".git")
            found.extend(os.path.relpath(os.path.join(base, n), self.consumer) for n in files)
        return sorted(found)


class CandidateIsSelfConsistentTests(unittest.TestCase):
    def test_the_manifest_at_the_candidate_commit_lists_exactly_its_composition_files(self):
        in_tree = git_out("ls-tree", "-r", "--name-only", CANDIDATE.commit, "--", *COMPOSITION_DIRS).decode().split()
        self.assertEqual(sorted(CANDIDATE.manifest), sorted(in_tree))

    def test_the_candidate_has_something_to_install_and_something_that_is_only_referenced(self):
        self.assertGreater(len(CANDIDATE.installed), 10)
        self.assertTrue(CANDIDATE.skipped)
        self.assertEqual(sorted(CANDIDATE.installed + CANDIDATE.skipped), sorted(CANDIDATE.manifest))


class MaterializeCandidateTests(MaterializedCase):
    def test_the_installed_file_set_is_exactly_the_manifest_entries_under_the_install_roots(self):
        doc = self.materialize()
        self.assertEqual(sorted(doc["result"]["written"]), CANDIDATE.installed)
        self.assertEqual(sorted(f["path"] for f in doc["files"]), CANDIDATE.installed)
        self.assertEqual(self.installed_on_disk(),
                         sorted(CANDIDATE.installed + [".tooltempest.lock", ".tooltempest.receipt.json"]))

    def test_every_installed_file_has_the_bytes_and_the_mode_of_the_candidate_commit(self):
        self.materialize()
        for rel in CANDIDATE.installed:
            path = self.consumer / rel
            with self.subTest(rel):
                self.assertEqual(path.read_bytes(), CANDIDATE.bytes_of(rel))
                mode = CANDIDATE.blobs[rel][0]
                self.assertEqual(bool(path.stat().st_mode & stat.S_IXUSR), mode == "100755", mode)
                self.assertFalse(path.is_symlink())

    def test_the_skipped_entries_are_named_with_a_reason_and_nothing_of_them_is_written(self):
        doc = self.materialize()
        self.assertEqual(sorted(s["path"] for s in doc["skipped"]), CANDIDATE.skipped)
        for entry in doc["skipped"]:
            self.assertTrue(entry["reason"], entry)
        for rel in CANDIDATE.skipped:
            self.assertFalse((self.consumer / rel).exists(), rel)
        for top in ("skills", "rules", "docs"):
            self.assertFalse((self.consumer / top).exists(), top)

    def test_the_receipt_records_the_commit_the_paths_the_hashes_the_blobs_and_the_modes(self):
        self.materialize()
        receipt = load(self.consumer / ".tooltempest.receipt.json")
        self.assertEqual(validate(receipt, load(RECEIPT_SCHEMA)), [])
        self.assertEqual((receipt["state"], receipt["commit"]), ("complete", CANDIDATE.commit))
        self.assertEqual(receipt["repository"], "https://tooltempest.invalid/tooltempest.git")
        self.assertEqual(sorted(f["path"] for f in receipt["files"]), CANDIDATE.installed)
        for entry in receipt["files"]:
            rel = entry["path"]
            with self.subTest(rel):
                self.assertEqual(entry["sha256"], hashlib.sha256(CANDIDATE.bytes_of(rel)).hexdigest())
                self.assertEqual((entry["mode"], entry["git_blob"]), CANDIDATE.blobs[rel])

    def test_verify_confirms_the_installation_on_every_property_and_a_second_apply_changes_nothing(self):
        self.materialize()
        code, doc = self.tooling("verify")
        self.assertEqual(code, 0, doc["findings"])
        self.assertEqual((doc["summary"]["local_integrity"], doc["summary"]["upstream_provenance"]), ("ok", "ok"))
        self.assertEqual(sorted(f["path"] for f in doc["files"]), CANDIDATE.installed)
        before = {rel: (self.consumer / rel).read_bytes() for rel in CANDIDATE.installed}
        code, again = self.tooling("materialize", "--apply")
        self.assertEqual((code, again["result"]["written"]), (0, []))
        self.assertEqual(before, {rel: (self.consumer / rel).read_bytes() for rel in CANDIDATE.installed})

    def test_a_tampered_installed_file_is_caught_by_verify_against_the_candidate(self):
        self.materialize()
        victim = self.consumer / CANDIDATE.installed[0]
        victim.write_bytes(victim.read_bytes() + b"# tampered\n")
        code, doc = self.tooling("verify")
        self.assertEqual(code, 1)
        self.assertEqual(doc["summary"]["local_integrity"], "failed")

    def test_materializing_needs_no_network_and_writes_nothing_outside_the_consumer(self):
        before_home = sorted(os.listdir(self.home))
        doc = self.materialize()
        self.assertEqual(doc["network"], {"allowed": False, "used": False})
        self.assertEqual(sorted(os.listdir(self.home)), before_home)


class MaterializedCopyRunsTests(MaterializedCase):
    """The distribution must work where it lands: every module the entry points import is shipped."""

    def setUp(self):
        super().setUp()
        self.materialize()
        subprocess.run(["git", "-C", str(self.consumer), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "commit", "-q", "--allow-empty", "-m", "base"], check=True, env=clean_env())

    def test_the_installed_tooling_verifies_its_own_installation(self):
        code, doc = self.tooling("verify", script=self.consumer / "scripts" / "tooling.py")
        self.assertEqual(code, 0, doc["findings"])

    def test_the_installed_docops_runs_check_and_report_without_a_spec(self):
        script = self.consumer / "scripts" / "docops.py"
        for command in ("check", "report"):
            with self.subTest(command):
                code, out, err = run_python([script, command, "--repo", self.consumer, "--tree", "HEAD"],
                                            cwd=self.home)
                self.assertEqual((code, err), (0, ""), out)
                if command == "check":
                    result = json.loads(out)
                    self.assertEqual(result["snapshots"][0]["status"], "N/A")
                    self.assertEqual(validate(result, load(REPO_ROOT / "schemas" / "docops-result.schema.json")), [])
                else:
                    self.assertIn("summary:", out)

    def test_the_installed_legacy_scripts_import_and_the_manifest_check_runs(self):
        scripts = self.consumer / "scripts"
        code, out, err = run_python(["-c", "import sys; sys.path.insert(0, %r); import doc_sync, doc_sync_tier2; "
                                     "print('ok')" % str(scripts)], cwd=self.home)
        self.assertEqual((code, out.strip()), (0, "ok"), err)
        # the consumer has no MANIFEST.txt of its own: the checker fails cleanly, it does not crash
        code, out, err = run_python([scripts / "check_manifest.py"], cwd=self.consumer)
        self.assertEqual(code, 1)
        self.assertIn("MANIFEST.txt not found", err)
        self.assertNotIn("Traceback", err)

    def test_every_python_module_in_the_installed_tree_compiles_and_every_schema_is_json(self):
        for rel in CANDIDATE.installed:
            path = self.consumer / rel
            with self.subTest(rel):
                if rel.endswith(".py"):
                    compile(path.read_text(encoding="utf-8"), rel, "exec")
                elif rel.endswith(".json"):
                    json.loads(path.read_text(encoding="utf-8"))


class IncompleteManifestTests(MaterializedCase):
    """The failure the manifest check exists to prevent, shown end to end.

    A copy of the candidate in which MANIFEST.txt forgets one module that
    `docops.py` imports: `check_manifest.py` names it, and a consumer that
    materializes from that copy gets a `docops.py` that cannot run."""

    FORGOTTEN = "scripts/docops_core/specparse.py"

    def make_damaged_copy(self):
        archive = git_out("archive", "--format=tar", CANDIDATE.commit, "--", "scripts", "schemas", "MANIFEST.txt")
        copy = self.tempdir() / "damaged"
        copy.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(copy)
        manifest = (copy / "MANIFEST.txt").read_text().splitlines()
        self.assertIn(self.FORGOTTEN, manifest)
        (copy / "MANIFEST.txt").write_text("\n".join(p for p in manifest if p != self.FORGOTTEN) + "\n")
        env = clean_env()
        for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@example.invalid",
                                                      "commit", "-q", "-m", "damaged candidate"]):
            subprocess.run(["git", "-C", str(copy), *args], check=True, env=env)
        commit = subprocess.run(["git", "-C", str(copy), "rev-parse", "HEAD"], capture_output=True, text=True,
                                env=env, check=True).stdout.strip()
        return copy, commit

    def test_check_manifest_names_the_forgotten_module(self):
        copy, _ = self.make_damaged_copy()
        code, out, err = run_python([copy / "scripts" / "check_manifest.py"], cwd=copy)
        self.assertEqual(code, 1)
        self.assertIn("Tracked but missing from MANIFEST.txt:", err)
        self.assertIn("  " + self.FORGOTTEN, err)

    def test_a_consumer_of_that_copy_gets_a_docops_that_cannot_run_and_says_so(self):
        copy, commit = self.make_damaged_copy()
        (self.consumer / ".tooltempest.lock").write_text(json.dumps({
            "repository": "https://tooltempest.invalid/tooltempest.git", "commit": commit}) + "\n")
        proc = subprocess.run([sys.executable, "-I", str(TOOLING), "materialize", "--apply", "--root", str(self.consumer),
                               "--source", str(copy), "--format", "json"], capture_output=True,
                              env=clean_env({"HOME": str(self.home)}), check=False)
        self.assertEqual(proc.returncode, 0, proc.stdout.decode()[:300])
        self.assertFalse((self.consumer / self.FORGOTTEN).exists())
        subprocess.run(["git", "-C", str(self.consumer), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "commit", "-q", "--allow-empty", "-m", "base"], check=True, env=clean_env())
        code, out, err = run_python([self.consumer / "scripts" / "docops.py", "check", "--repo", self.consumer,
                                     "--tree", "HEAD"], cwd=self.home)
        self.assertEqual(code, 2)
        result = json.loads(out)
        self.assertIsNone(result["summary"]["status"])
        self.assertIn("could not be imported", result["summary"]["reason"])


if __name__ == "__main__":
    unittest.main()
