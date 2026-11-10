"""The contract root (ADR-0017, ADR-0020): the consumer contract and the universal rules, each listed in the
manifest and delivered by the consumer target. The contract is the single source of the consumer obligation;
the universal rules point to it and do not repeat it.

The checks read the candidate commit's Git objects, not the working tree.
"""
import json
import os
import subprocess
import sys
import unittest

from dist_helpers import REPO_ROOT, TempDirCase, candidate_commit, clean_env, git_out

CONTRACT = "docs/tooltempest/consumer-contract.md"
RULES = "docs/tooltempest/universal-rules.md"


def show(path):
    return git_out("show", "%s:%s" % (candidate_commit(), path)).decode("utf-8")


class ContractFileTests(TempDirCase):
    def test_the_contract_root_holds_the_contract_and_the_rules_and_both_are_in_the_manifest(self):
        manifest = show("MANIFEST.txt").splitlines()
        in_root = [p for p in manifest if p.startswith("docs/tooltempest/")]
        self.assertEqual(in_root, [CONTRACT, RULES])
        tree = git_out("ls-tree", "-r", "--name-only", candidate_commit(), "--", "docs/tooltempest/").decode().split()
        self.assertEqual(tree, [CONTRACT, RULES])
        self.assertFalse(CONTRACT.startswith("docs/reference/"))

    def test_the_contract_states_the_obligation_and_the_repin_evidence_per_component(self):
        text = show(CONTRACT)
        for needle in ("ToolTempest consumer obligation", "scripts/check_manifest.py", "Updating a consumer's pin",
                       "The repin evidence is per component", "authorizes the repin itself",
                       "The checker of the new commit", "The payload paths", "The materialize dry run"):
            self.assertIn(needle, text)

    def test_the_rules_point_to_the_contract_and_do_not_repeat_it(self):
        rules = show(RULES)
        item = rules[rules.index("## ToolTempest consumer obligation"):]
        self.assertIn("docs/tooltempest/consumer-contract.md", item)
        self.assertNotIn("payload paths", rules)
        self.assertEqual(rules.count("Updating a consumer's pin"), 0, "the repin text has one source")
        self.assertIn("## How every repository works", rules)

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
        self.assertEqual((consumer / RULES).read_bytes(), show(RULES).encode("utf-8"))
        self.assertEqual(json.loads((consumer / ".tooltempest.receipt.json").read_text())["schema_version"], 2)
        ignore = subprocess.run(["git", "-C", str(consumer), "check-ignore", "-q", CONTRACT], env=clean_env())
        self.assertEqual(ignore.returncode, 1, "the contract file is ignored in a consumer without ignore rules")
        ignore = subprocess.run(["git", "-C", str(consumer), "check-ignore", "-q", RULES], env=clean_env())
        self.assertEqual(ignore.returncode, 1, "the rules file is ignored in a consumer without ignore rules")


if __name__ == "__main__":
    unittest.main()
