"""SPEC registry parsing: strict, fail-closed."""
import json
import unittest

from helpers import DocopsTestCase  # noqa: F401  (sys.path setup)

from docops_core import policy
from docops_core.policy import PolicyError, parse_policy


def doc(**overrides):
    base = {"schema_version": 1, "specs": [{"path": "SPEC.md", "track": "runner"}]}
    base.update(overrides)
    return json.dumps(base).encode()


class PolicyParseTests(DocopsTestCase):
    def test_valid_multi_track_policy(self):
        data = doc(specs=[{"path": "SPEC.md", "track": "runner"},
                          {"path": "verification/SPEC.md", "track": "verification"}])
        parsed = parse_policy(data)
        self.assertEqual(parsed.source, "snapshot")
        self.assertEqual([(t.path, t.track) for t in parsed.tracks],
                         [("SPEC.md", "runner"), ("verification/SPEC.md", "verification")])
        self.assertFalse(parsed.implicit)

    def test_empty_registry_is_valid(self):
        self.assertEqual(parse_policy(doc(specs=[])).tracks, ())

    def test_default_policy_is_implicit_root(self):
        default = policy.default_policy()
        self.assertTrue(default.implicit)
        self.assertEqual([(t.path, t.track) for t in default.tracks], [("SPEC.md", "root")])

    def test_invalid_documents_are_rejected(self):
        bad = {
            "not json": b"{nope",
            "empty": b"",
            "invalid utf-8": b"\xff\xfe{}",
            "array": b"[]",
            "string": b'"x"',
            "missing version": json.dumps({"specs": []}).encode(),
            "wrong version": doc(schema_version=2),
            "bool version": doc(schema_version=True),
            "string version": doc(schema_version="1"),
            "missing specs": json.dumps({"schema_version": 1}).encode(),
            "specs not list": doc(specs={}),
            "extra top key": doc(extra=1),
            "entry not object": doc(specs=["SPEC.md"]),
            "entry extra key": doc(specs=[{"path": "SPEC.md", "track": "a", "state": "idle"}]),
            "entry missing track": doc(specs=[{"path": "SPEC.md"}]),
            "entry missing path": doc(specs=[{"track": "a"}]),
            "bad track chars": doc(specs=[{"path": "SPEC.md", "track": "Has Space"}]),
            "empty track": doc(specs=[{"path": "SPEC.md", "track": ""}]),
            "uppercase track": doc(specs=[{"path": "SPEC.md", "track": "Runner"}]),
            "trailing newline track": doc(specs=[{"path": "SPEC.md", "track": "root\n"}]),
            "duplicate path": doc(specs=[{"path": "SPEC.md", "track": "a"},
                                         {"path": "SPEC.md", "track": "b"}]),
            "duplicate track": doc(specs=[{"path": "a/SPEC.md", "track": "a"},
                                          {"path": "b/SPEC.md", "track": "a"}]),
            "duplicate json key": b'{"schema_version":1,"schema_version":1,"specs":[]}',
        }
        for label, data in bad.items():
            with self.assertRaises(PolicyError, msg=label):
                parse_policy(data)

    def test_unsafe_paths_are_rejected(self):
        for path in ("", "/abs/SPEC.md", "../SPEC.md", "a/../SPEC.md", "./SPEC.md", "a//SPEC.md",
                     "a/SPEC.md/", "a\\SPEC.md", ".git/SPEC.md", "a/.git/SPEC.md",
                     "a\nb/SPEC.md", "a\x00/SPEC.md", 5, None, "x" * 5000):
            with self.assertRaises(PolicyError, msg=repr(path)):
                parse_policy(doc(specs=[{"path": path, "track": "t"}]))

    def test_schema_descriptions_name_frontmatter_not_a_body_marker(self):
        import json
        from helpers import REPO_ROOT
        for name in ("docops-policy.schema.json", "docops-result.schema.json"):
            text = (REPO_ROOT / "schemas" / name).read_text(encoding="utf-8")
            json.loads(text)
            self.assertNotIn("<!--", text, name)
            self.assertNotIn("docops:spec-state", text, name)
        policy_text = (REPO_ROOT / "schemas" / "docops-policy.schema.json").read_text(encoding="utf-8")
        self.assertIn("spec-state: idle", policy_text)
        self.assertIn("frontmatter", policy_text)

    def test_bom_is_tolerated(self):
        self.assertEqual(len(parse_policy(b"\xef\xbb\xbf" + doc()).tracks), 1)


if __name__ == "__main__":
    unittest.main()
