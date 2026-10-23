"""Lock, manifest and receipt parsing, and the receipt schema against the parser.

The schema and parser are cross-checked: every receipt the schema rejects the
parser rejects too, and the other way round for the structural cases; a few
rules (paths, duplicates) are enforced by the parser alone, as the schema
states.
"""
import json
import unittest

from tooling_helpers import RECEIPT_SCHEMA, RESULT_SCHEMA, docops_helpers, load_schema, validate  # noqa: F401
from tooling_core import declarations
from tooling_core.declarations import FormatError, parse_lock, parse_manifest, parse_receipt, serialize_receipt

OID = "a" * 40
SHA = "b" * 64


def complete(**overrides):
    document = {"schema_version": 1, "tool": "tooltempest-tooling", "tool_version": "0.1.0",
                "state": "complete", "repository": "https://example.invalid/x.git", "commit": OID,
                "files": [{"path": "scripts/a.py", "sha256": SHA, "git_blob": OID, "mode": "100644"}]}
    document.update(overrides)
    return document


def installing(**overrides):
    document = complete(state="installing",
                        files=[{"path": "scripts/a.py", "sha256": SHA, "git_blob": OID, "mode": "100755",
                                "previous_sha256": None, "previous_mode": None}])
    document.update(overrides)
    return document


def encode(document):
    return json.dumps(document).encode()


class ReceiptContractTests(unittest.TestCase):
    schema = load_schema(RECEIPT_SCHEMA)

    ACCEPTED = {
        "complete": complete(),
        "installing": installing(),
        "installing with a previous hash": installing(files=[
            {"path": "scripts/a.py", "sha256": SHA, "git_blob": OID, "mode": "100644",
             "previous_sha256": "c" * 64, "previous_mode": "100755"}]),
        "no files": complete(files=[]),
        "sha256 object ids": complete(commit="d" * 64),
    }
    REJECTED = {
        "unknown top-level field": complete(extra=1),
        "missing repository": {k: v for k, v in complete().items() if k != "repository"},
        "schema_version 2": complete(schema_version=2),
        "bad state": complete(state="done"),
        "abbreviated commit": complete(commit="abc123"),
        "uppercase commit": complete(commit="A" * 40),
        "other tool name": complete(tool="something"),
        "empty tool_version": complete(tool_version=""),
        "bad sha256": complete(files=[{"path": "scripts/a.py", "sha256": "zz", "git_blob": OID, "mode": "100644"}]),
        "bad mode": complete(files=[{"path": "scripts/a.py", "sha256": SHA, "git_blob": OID, "mode": "100666"}]),
        "symlink mode": complete(files=[{"path": "scripts/a.py", "sha256": SHA, "git_blob": OID, "mode": "120000"}]),
        "unknown file field": complete(files=[{"path": "scripts/a.py", "sha256": SHA, "git_blob": OID,
                                               "mode": "100644", "x": 1}]),
        "missing file field": complete(files=[{"path": "scripts/a.py", "sha256": SHA, "mode": "100644"}]),
        "path outside the install roots": complete(files=[{"path": "skills/a.md", "sha256": SHA, "git_blob": OID,
                                                           "mode": "100644"}]),
        "installing without previous_sha256": installing(files=[{"path": "scripts/a.py", "sha256": SHA,
                                                                 "git_blob": OID, "mode": "100644"}]),
        "installing without previous_mode": installing(files=[{"path": "scripts/a.py", "sha256": SHA,
                                                               "git_blob": OID, "mode": "100644",
                                                               "previous_sha256": None}]),
        "bad previous_mode": installing(files=[{"path": "scripts/a.py", "sha256": SHA, "git_blob": OID,
                                                "mode": "100644", "previous_sha256": None,
                                                "previous_mode": "120000"}]),
        "complete with previous_sha256": complete(files=[{"path": "scripts/a.py", "sha256": SHA, "git_blob": OID,
                                                          "mode": "100644", "previous_sha256": None}]),
        "bad previous_sha256": installing(files=[{"path": "scripts/a.py", "sha256": SHA, "git_blob": OID,
                                                  "mode": "100644", "previous_sha256": "nope",
                                                  "previous_mode": "100644"}]),
        "files not a list": complete(files={}),
    }
    PARSER_ONLY = {
        "previous hash without previous mode": installing(files=[
            {"path": "scripts/a.py", "sha256": SHA, "git_blob": OID, "mode": "100644",
             "previous_sha256": "c" * 64, "previous_mode": None}]),
        "previous mode without previous hash": installing(files=[
            {"path": "scripts/a.py", "sha256": SHA, "git_blob": OID, "mode": "100644",
             "previous_sha256": None, "previous_mode": "100644"}]),
        "traversal": complete(files=[{"path": "scripts/../x", "sha256": SHA, "git_blob": OID, "mode": "100644"}]),
        "duplicate path": complete(files=[{"path": "scripts/a.py", "sha256": SHA, "git_blob": OID, "mode": "100644"}] * 2),
        "case collision": complete(files=[
            {"path": "scripts/a.py", "sha256": SHA, "git_blob": OID, "mode": "100644"},
            {"path": "scripts/A.py", "sha256": SHA, "git_blob": OID, "mode": "100644"}]),
        "file inside file": complete(files=[
            {"path": "scripts/a", "sha256": SHA, "git_blob": OID, "mode": "100644"},
            {"path": "scripts/a/b", "sha256": SHA, "git_blob": OID, "mode": "100644"}]),
        "backslash": complete(files=[{"path": "scripts/a\\b", "sha256": SHA, "git_blob": OID, "mode": "100644"}]),
        "git directory": complete(files=[{"path": "scripts/.git/x", "sha256": SHA, "git_blob": OID, "mode": "100644"}]),
        "boolean schema_version": complete(schema_version=True),
    }

    def test_what_the_schema_accepts_the_parser_accepts(self):
        for name, document in self.ACCEPTED.items():
            with self.subTest(name):
                self.assertEqual(validate(document, self.schema), [])
                parse_receipt(encode(document))

    def test_what_the_schema_rejects_the_parser_rejects(self):
        for name, document in self.REJECTED.items():
            with self.subTest(name):
                self.assertNotEqual(validate(document, self.schema), [], "the schema accepted it")
                with self.assertRaises(FormatError) as ctx:
                    parse_receipt(encode(document))
                self.assertEqual(ctx.exception.code, "RECEIPT_MALFORMED")

    def test_rules_the_schema_cannot_express_are_enforced_by_the_parser(self):
        for name, document in self.PARSER_ONLY.items():
            with self.subTest(name):
                with self.assertRaises(FormatError):
                    parse_receipt(encode(document))

    def test_a_receipt_round_trips_byte_for_byte(self):
        for document in (complete(), installing()):
            receipt = parse_receipt(encode(document))
            raw = serialize_receipt(receipt)
            self.assertEqual(serialize_receipt(parse_receipt(raw)), raw)
            self.assertEqual(validate(json.loads(raw), self.schema), [])
            self.assertTrue(raw.endswith(b"\n"))
            parsed = json.loads(raw)
            self.assertEqual(list(parsed), sorted(parsed), "keys are written in a stable order")
            for entry in parsed["files"]:
                self.assertEqual(list(entry), sorted(entry))

    def test_garbage_is_a_format_error_never_another_exception(self):
        for raw in (b"", b"\xff\xfe", b"[]", b"null", b"{", b'{"a": 1, "a": 2}', b"1", b'"x"'):
            with self.subTest(raw):
                with self.assertRaises(FormatError):
                    parse_receipt(raw)


class ResultSchemaTests(unittest.TestCase):
    def test_the_schema_is_valid_json_with_the_documented_exit_codes(self):
        schema = load_schema(RESULT_SCHEMA)
        self.assertEqual(schema["properties"]["summary"]["properties"]["exit_code"]["enum"], [0, 1, 2])
        self.assertEqual(schema["properties"]["command"]["enum"], ["verify", "status", "materialize"])

    def test_a_verify_exit_zero_cannot_conceal_a_failed_property(self):
        schema = load_schema(RESULT_SCHEMA)
        base = {"schema_version": 1, "tool": {"name": "tooltempest-tooling", "version": "0.1.0"},
                "command": "verify", "root": "/r", "pin": None, "source": None,
                "network": {"allowed": False, "used": False},
                "receipt": {"present": False, "state": None, "commit": None, "matches_pin": None},
                "summary": {"local_integrity": "ok", "upstream_provenance": "ok", "freshness": "unknown",
                            "errors": 0, "exit_code": 0},
                "freshness": None, "files": [], "skipped": [], "findings": [], "error": None}
        self.assertEqual(validate(base, schema), [])
        for key, value in (("local_integrity", "failed"), ("upstream_provenance", "unknown"), ("errors", 1)):
            broken = json.loads(json.dumps(base))
            broken["summary"][key] = value
            self.assertNotEqual(validate(broken, schema), [], key)
        broken = json.loads(json.dumps(base))
        broken["error"] = {"code": "X", "message": "y"}
        self.assertNotEqual(validate(broken, schema), [], "an error object with exit code 0")
        broken = json.loads(json.dumps(base))
        broken["summary"]["exit_code"] = 2
        self.assertNotEqual(validate(broken, schema), [], "exit code 2 without an error object")


class ManifestParsingTests(unittest.TestCase):
    def test_a_normal_manifest_is_split_into_installed_and_skipped_paths(self):
        manifest = parse_manifest(b"docs/reference/r.md\nrules/x.md\nschemas/s.json\nscripts/a.py\nskills/k/SKILL.md\n")
        self.assertEqual(manifest.install, ["schemas/s.json", "scripts/a.py"])
        self.assertEqual([p for p, _ in manifest.skipped], ["docs/reference/r.md", "rules/x.md", "skills/k/SKILL.md"])

    def test_blank_lines_are_ignored_but_a_manifest_of_nothing_is_not_a_manifest(self):
        self.assertEqual(parse_manifest(b"\n\nscripts/a.py\n\n").install, ["scripts/a.py"])
        with self.assertRaises(FormatError):
            parse_manifest(b"\n\n")

    def test_crlf_line_endings_are_rejected_not_silently_stripped(self):
        with self.assertRaises(FormatError):
            parse_manifest(b"scripts/a.py\r\nscripts/b.py\r\n")


class LockParsingTests(unittest.TestCase):
    def test_a_legacy_lock_declares_its_single_file(self):
        lock = parse_lock(encode({"repository": "r", "commit": OID, "path": "scripts/a.py", "sha256": SHA}))
        self.assertEqual(lock.declared, {"scripts/a.py": SHA})

    def test_a_lock_with_no_declarations_is_valid(self):
        self.assertEqual(parse_lock(encode({"repository": "r", "commit": OID})).declared, {})

    def test_the_real_consumer_lock_shape_is_accepted(self):
        text = ('{\n  "repository": "https://github.com/mikkiola/tooltempest.git",\n'
                '  "commit": "de4436adc4540593fec52978b0e5ad5b7b949d9e",\n  "path": "scripts/doc_sync.py",\n'
                '  "sha256": "663f019d987669bd9afb7a66e1fac2c5eb0f647835849faadfd8cd5cc4e25d7a"\n}\n')
        self.assertEqual(list(parse_lock(text.encode()).declared), ["scripts/doc_sync.py"])

    def test_oversized_input_is_a_format_error(self):
        with self.assertRaises(FormatError):
            parse_lock(b" " * (declarations.MAX_TEXT_BYTES + 1))


class MoreParsingTests(unittest.TestCase):
    def test_an_oversized_manifest_is_rejected(self):
        with self.assertRaises(FormatError):
            parse_manifest(b"scripts/a.py\n" + b"\n" * declarations.MAX_TEXT_BYTES)  # valid but for its size

    def test_an_empty_repository_value_in_the_lock_is_rejected(self):
        with self.assertRaises(FormatError):
            parse_lock(encode({"repository": "", "commit": OID}))

    def test_a_git_directory_component_is_refused_in_any_case(self):
        for path in ("scripts/.git/x", "scripts/.GIT/x", "scripts/.Git"):
            self.assertIsNotNone(declarations.path_problem(path), path)
        self.assertIsNone(declarations.path_problem("scripts/.gitkeep"))
        self.assertIsNone(declarations.path_problem("scripts/git/x"))
