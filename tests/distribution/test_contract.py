"""The consumer contract (ADR-0017): one file in its own root, listed in the manifest, delivered by the
consumer target, and the single source of the consumer obligation that item 7 of the documentation rules
used to repeat.

The checks read the candidate commit's Git objects, not the working tree.
"""
import json
import os
import subprocess
import sys
import unittest

from dist_helpers import REPO_ROOT, TempDirCase, candidate_commit, clean_env, git_out

CONTRACT = "docs/tooltempest/consumer-contract.md"
RULES = "docs/reference/documentation-rules.md"


def show(path):
    return git_out("show", "%s:%s" % (candidate_commit(), path)).decode("utf-8")


class ContractFileTests(TempDirCase):
    def test_the_contract_is_one_tracked_file_listed_in_the_manifest_in_its_own_root(self):
        manifest = show("MANIFEST.txt").splitlines()
        in_root = [p for p in manifest if p.startswith("docs/tooltempest/")]
        self.assertEqual(in_root, [CONTRACT])
        tree = git_out("ls-tree", "-r", "--name-only", candidate_commit(), "--", "docs/tooltempest/").decode().split()
        self.assertEqual(tree, [CONTRACT])
        self.assertFalse(CONTRACT.startswith("docs/reference/"))

    def test_the_contract_states_the_obligation_and_the_repin_evidence_per_component(self):
        text = show(CONTRACT)
        for needle in ("ToolTempest consumer obligation", "scripts/check_manifest.py", "Updating a consumer's pin",
                       "The repin evidence is per component", "needs the Owner's approval of the diff",
                       "The checker of the new commit", "The payload paths", "The materialize dry run"):
            self.assertIn(needle, text)

    def test_item_7_of_the_rules_points_to_the_contract_and_no_longer_repeats_it(self):
        rules = show(RULES)
        start = rules.index("7. **ToolTempest consumer obligation**")
        item = rules[start:rules.index("8. **Conditional rules**")]
        self.assertIn("docs/tooltempest/consumer-contract.md", item)
        self.assertIn("Dated note (2026-11-04", item)
        self.assertNotIn("Updating a consumer's pin", item)
        self.assertNotIn("payload paths", item)
        self.assertEqual(rules.count("Updating a consumer's pin"), 0, "the repin text has one source")

    def test_the_consumer_target_delivers_the_contract_and_the_client_target_does_not(self):
        home = self.tempdir()
        consumer = self.tempdir() / "consumer"
        consumer.mkdir()
        subprocess.run(["git", "init", "-q", str(consumer)], check=True, env=clean_env())
        (consumer / ".tooltempest.lock").write_text(json.dumps({
            "repository": "https://tooltempest.invalid/tooltempest.git", "commit": candidate_commit()}) + "\n")
        proc = subprocess.run([sys.executable, "-I", str(REPO_ROOT / "scripts" / "tooling.py"), "materialize",
                               "--apply", "--root", str(consumer), "--source", str(REPO_ROOT), "--format", "json"],
                              capture_output=True, env=clean_env({"HOME": str(home)}), check=False)
        doc = json.loads(proc.stdout.decode())
        self.assertEqual(proc.returncode, 0, doc["findings"])
        self.assertEqual((consumer / CONTRACT).read_bytes(), show(CONTRACT).encode("utf-8"))
        self.assertEqual(json.loads((consumer / ".tooltempest.receipt.json").read_text())["schema_version"], 2)
        ignore = subprocess.run(["git", "-C", str(consumer), "check-ignore", "-q", CONTRACT], env=clean_env())
        self.assertEqual(ignore.returncode, 1, "the contract file is ignored in a consumer without ignore rules")


if __name__ == "__main__":
    unittest.main()
