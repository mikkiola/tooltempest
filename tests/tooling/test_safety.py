"""Malformed manifests, path traversal, symlink and ancestor escape, unsupported
source types: each is refused before any destination is written, with the
reason named, and nothing outside the consumer checkout is ever touched.
"""
import os
from unittest import mock

from tooling_helpers import ToolingCase, engine


class ManifestTests(ToolingCase):
    def publish_with_manifest(self, manifest, files=None):
        files = files if files is not None else {"scripts/a.py": "a\n", "scripts/b.py": "b\n"}
        commit = self.upstream.publish(files, manifest=manifest, message="manifest")
        self.consumer.write_lock(commit)
        return commit

    def assertRefused(self, code_expected, needle=None):
        before = self.digest(self.consumer.root)
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 1, doc["findings"])
        self.assertIn(code_expected, self.codes(doc, "error"))
        if needle:
            self.assertIn(needle, " ".join(f["message"] for f in doc["findings"]))
        self.assertEqual(doc["result"]["written"], [])
        self.assertEqual(self.digest(self.consumer.root), before)
        code, doc = self.verify()
        self.assertEqual(code, 1)
        self.assertNotEqual(doc["summary"]["local_integrity"], "ok")
        self.assertNotEqual(doc["summary"]["upstream_provenance"], "ok")
        return doc

    def test_malformed_manifest_lines_are_rejected_whole(self):
        cases = {
            "": "lists no files",
            "\n\n": "lists no files",
            "/etc/passwd\n": "absolute",
            "scripts/../../x\n": "'..'",
            "../x\n": "'..'",
            "scripts//a.py\n": "empty",
            "scripts/./a.py\n": "'.'",
            "scripts\\a.py\n": "backslash",
            "scripts/a.py/\n": "empty",
            "scripts/.git/hook\n": ".git",
            "scripts/a\tb.py\n": "control",
            "scripts/a.py\nscripts/a.py\n": "duplicate",
            "scripts/a.py\nscripts/A.PY\n": "collide",
            "scripts/\u00e9.py\nscripts/e\u0301.py\n": "collide",
            "scripts/a.py\nscripts/a.py/b.py\n": "inside the file destination",
            "README.md\n": "no destination mapping",
            "tests/x.py\n": "no destination mapping",
            "scripts\n": "no destination mapping",
            "skills\n": "no destination mapping",
            "scripts/a.py\nunknown/thing\n": "no destination mapping",
        }
        for manifest, needle in cases.items():
            with self.subTest(manifest):
                self.publish_with_manifest(manifest)
                self.assertRefused("MANIFEST_MALFORMED", needle)

    def test_invalid_utf8_in_the_manifest_is_rejected(self):
        self.publish_with_manifest(b"scripts/a.py\n\xff\xfe\n")
        self.assertRefused("MANIFEST_MALFORMED", "UTF-8")

    def test_a_pinned_commit_without_a_manifest_is_refused(self):
        commit = self.upstream.fx.commit_files({"scripts/a.py": "a\n"}, "no manifest at all")
        self.consumer.write_lock(commit)
        self.assertRefused("MANIFEST_MISSING")

    def test_a_manifest_that_is_a_directory_or_symlink_is_refused(self):
        self.upstream.fx.stage("MANIFEST.txt/inner", "x")
        commit = self.upstream.fx.commit("manifest is a directory")
        self.consumer.write_lock(commit)
        self.assertRefused("MANIFEST_MISSING")

    def test_a_listed_file_that_is_absent_at_the_pin_is_refused_as_a_whole(self):
        self.publish_with_manifest("scripts/a.py\nscripts/ghost.py\n")
        self.assertRefused("SOURCE_FILE_MISSING", "ghost.py")
        self.assertFalse(self.consumer.exists("scripts/a.py"), "a partial installation happened")

    def test_unsupported_source_types_are_refused(self):
        blob = self.upstream.fx.git("hash-object", "-w", "--stdin", input_bytes=b"target.py")
        for mode, label in (("120000", "symlink"), ("160000", "submodule")):
            with self.subTest(label):
                self.upstream.publish({"scripts/a.py": "a\n"}, message="base")
                oid = blob if mode == "120000" else self.upstream.fx.git("rev-parse", "HEAD")
                commit = self.upstream.add_raw_entry(mode, "scripts/odd.py", oid)
                self.upstream.fx.stage("MANIFEST.txt", "scripts/a.py\nscripts/odd.py\n")
                commit = self.upstream.fx.commit("manifest lists the odd entry")
                self.consumer.write_lock(commit)
                self.assertRefused("SOURCE_FILE_UNSUPPORTED", label if label == "symlink" else "submodule")
                self.upstream.fx.git("rm", "-q", "--cached", "scripts/odd.py")

    def test_a_listed_directory_is_unsupported(self):
        self.publish_with_manifest("scripts/a.py\nscripts/sub\n", files={"scripts/a.py": "a\n", "scripts/sub/x.py": "x\n"})
        self.assertRefused("SOURCE_FILE_UNSUPPORTED", "directory")


class DestinationSafetyTests(ToolingCase):
    def setUp(self):
        super().setUp()
        self.publish_v1()
        self.outside = os.path.join(self.home, "outside")
        os.makedirs(self.outside)
        with open(os.path.join(self.outside, "alpha.py"), "w") as handle:
            handle.write("precious\n")

    def assertNothingEscaped(self):
        with open(os.path.join(self.outside, "alpha.py")) as handle:
            self.assertEqual(handle.read(), "precious\n")
        self.assertEqual(sorted(os.listdir(self.outside)), ["alpha.py"])

    def refuse(self, expected="PATH_UNSAFE", needle=None):
        before = self.digest(self.consumer.root)
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 1)
        self.assertIn(expected, self.codes(doc, "error"))
        if needle:
            self.assertIn(needle, " ".join(f["message"] for f in doc["findings"]))
        self.assertEqual(doc["result"]["written"], [])
        self.assertNothingEscaped()
        self.assertEqual(self.digest(self.consumer.root), before)
        return doc

    def test_a_symlinked_ancestor_cannot_redirect_the_installation(self):
        os.symlink(self.outside, self.consumer.path("scripts"))
        doc = self.refuse()
        self.assertIn("symlink", " ".join(f["message"] for f in doc["findings"]))
        code, vdoc = self.verify()
        self.assertEqual(code, 1)
        self.assertEqual(self.file(vdoc, "scripts/alpha.py")["local"]["status"], "unsafe")
        self.assertEqual(vdoc["summary"]["local_integrity"], "failed")
        self.assertIn("PATH_UNSAFE", self.codes(vdoc, "error"))

    def test_a_symlinked_destination_file_is_not_followed_or_replaced(self):
        os.makedirs(self.consumer.path("scripts"))
        os.symlink(os.path.join(self.outside, "alpha.py"), self.consumer.path("scripts/alpha.py"))
        self.refuse(needle="the destination is a symlink")
        self.assertTrue(os.path.islink(self.consumer.path("scripts/alpha.py")))

    def test_a_non_directory_ancestor_is_refused(self):
        self.consumer.write("schemas", "i am a file, not a directory\n")
        self.refuse(needle="a non-directory is in the way")
        self.assertEqual(self.consumer.read("schemas"), b"i am a file, not a directory\n")

    def test_a_directory_where_a_file_belongs_is_refused(self):
        os.makedirs(self.consumer.path("scripts/alpha.py"))
        self.refuse(needle="the destination is not a regular file")
        self.assertTrue(os.path.isdir(self.consumer.path("scripts/alpha.py")))

    def test_the_root_itself_may_be_reached_through_a_symlink_but_not_escaped_from(self):
        link = os.path.join(self.home, "link-to-consumer")
        os.symlink(self.consumer.root, link)
        code, doc = self.materialize("--apply", root=link)
        self.assertEqual(code, 0, doc["findings"])
        self.assertTrue(self.consumer.exists("scripts/alpha.py"))

    def test_a_symlinked_receipt_path_is_never_followed(self):
        target = os.path.join(self.outside, "receipt.json")
        os.symlink(target, self.consumer.receipt_path)
        before = self.digest(self.consumer.root)
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 1)
        self.assertIn("RECEIPT_MALFORMED", self.codes(doc, "error"))
        self.assertFalse(os.path.exists(target))
        self.assertEqual(self.digest(self.consumer.root), before)
        self.assertNothingEscaped()

    def test_a_symlinked_ancestor_appearing_between_staging_and_install_is_caught(self):
        # the destination is re-inspected under the lock immediately before each rename
        real_replace = os.replace
        swapped = []

        def swap_after_the_journal(src, dst, **kw):
            real_replace(src, dst, **kw)
            if dst == ".tooltempest.receipt.json" and not swapped:
                swapped.append(True)
                os.rename(self.consumer.path("scripts"), self.consumer.path("scripts.real"))
                os.symlink(self.outside, self.consumer.path("scripts"))

        with mock.patch.object(engine.os, "replace", side_effect=swap_after_the_journal):
            doc = engine.run_materialize(self.engine_options(apply=True))
        self.assertEqual(doc["summary"]["exit_code"], 1)
        self.assertIn("INSTALL_INTERRUPTED", self.codes(doc, "error"))
        self.assertNothingEscaped()
        self.assertEqual(self.consumer.receipt()["state"], "installing")
        # the user undoes the swap; the repeated run finishes the job
        os.unlink(self.consumer.path("scripts"))
        os.rename(self.consumer.path("scripts.real"), self.consumer.path("scripts"))
        # the run removed its own staged copies through the descriptor it held, in the real directory
        self.assertEqual([n for n in os.listdir(self.consumer.path("scripts")) if n.startswith(".tooltempest-stage")], [])
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 0, doc["findings"])
        self.assertEqual(self.verify()[0], 0)
        self.assertEqual([f for f in doc["findings"] if f["code"] == "STAGING_FILE_PRESERVED"], [])


class ManifestAcceptanceTests(ToolingCase):
    def test_sibling_names_sharing_a_prefix_are_not_an_overlap(self):
        commit = self.upstream.publish({"scripts/a": "a\n", "scripts/ab": "ab\n", "scripts/a.py": "x\n"},
                                       message="siblings")
        self.consumer.write_lock(commit)
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 0, doc["findings"])
        self.assertEqual(self.verify()[0], 0)

    def test_blank_lines_in_the_manifest_are_ignored(self):
        commit = self.upstream.publish({"scripts/a.py": "a\n"}, manifest="\n\nscripts/a.py\n\n", message="blank")
        self.consumer.write_lock(commit)
        self.assertEqual(self.materialize("--apply")[0], 0)


class OnlyUnsafeFilesTests(ToolingCase):
    def test_unsafe_files_alone_fail_local_integrity(self):
        self.publish_v1()
        self.apply()
        outside = os.path.join(self.home, "elsewhere")
        os.makedirs(outside)
        os.rename(self.consumer.path("scripts"), self.consumer.path("scripts.moved"))
        os.symlink(outside, self.consumer.path("scripts"))
        code, doc = self.verify()
        self.assertEqual(code, 1)
        self.assertEqual({r["local"]["status"] for r in doc["files"]}, {"ok", "unsafe"})
        self.assertEqual(doc["summary"]["local_integrity"], "failed")
