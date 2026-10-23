"""Verified object access: the pieces that a healthy Git cannot be made to exercise.

A real repository never serves a truncated stream or a tree with a repeated
name, so those cases are driven with crafted bytes and a fake `cat-file`
process. Real-repository behaviour (tampered loose objects, wrong types,
dirty caches) is in test_verify.py.
"""
import hashlib
import io
import os
import subprocess
import unittest
from unittest import mock

from tooling_helpers import ToolingCase, clean_env
from tooling_core import objects
from tooling_core.objects import (GitFailure, ObjectCorrupt, ObjectMissing, ObjectStore, TreeEntry,
                                  fetch_url_problem, is_oid, parse_tree)

OID1 = "1" * 40
OID2 = "2" * 40


def tree_bytes(*entries):
    return b"".join(mode.encode() + b" " + name + b"\0" + bytes.fromhex(oid) for mode, name, oid in entries)


class ParseTreeTests(unittest.TestCase):
    def test_a_well_formed_tree_is_parsed(self):
        entries = parse_tree(tree_bytes(("100644", b"a.py", OID1), ("40000", b"sub", OID2)), 40)
        self.assertEqual(entries, {b"a.py": TreeEntry("100644", OID1), b"sub": TreeEntry("40000", OID2)})

    def test_sha256_trees_are_parsed_with_longer_ids(self):
        long_oid = "3" * 64
        self.assertEqual(parse_tree(tree_bytes(("100755", b"x", long_oid)), 64)[b"x"], TreeEntry("100755", long_oid))

    def test_an_empty_tree_is_valid(self):
        self.assertEqual(parse_tree(b"", 40), {})

    def test_malformed_trees_are_corruption_never_a_partial_result(self):
        good = tree_bytes(("100644", b"a.py", OID1))
        cases = {
            "truncated id": good[:-3],
            "truncated by one byte": good[:-1],
            "no nul": b"100644 a.py",
            "no space": b"100644a.py\0" + bytes.fromhex(OID1),
            "empty name": tree_bytes(("100644", b"", OID1)),
            "repeated name": tree_bytes(("100644", b"a.py", OID1), ("100644", b"a.py", OID2)),
            "id of the wrong length": tree_bytes(("100644", b"a.py", OID1)) + b"100644 b\0" + b"\x01\x02",
        }
        for name, data in cases.items():
            with self.subTest(name):
                with self.assertRaises(ObjectCorrupt):
                    parse_tree(data, 40)


class OidTests(unittest.TestCase):
    def test_only_full_lowercase_hex_object_ids_are_ids(self):
        for ok in ("a" * 40, "0" * 64):
            self.assertTrue(is_oid(ok))
        for bad in ("A" * 40, "a" * 39, "a" * 41, "a" * 63, "", None, 5, "g" * 40, "a" * 40 + "\n", "main"):
            self.assertFalse(is_oid(bad), bad)


class FakeBatch:
    """A stand-in for the long-lived `git cat-file --batch` process."""

    def __init__(self, reply):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(reply)

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


class StreamProtocolTests(ToolingCase):
    def store_with(self, reply):
        self.upstream.publish({"scripts/a.py": "a\n"})
        store = ObjectStore(self.upstream.root)
        store._batch = FakeBatch(reply)
        return store

    def test_a_truncated_object_is_a_git_failure_not_data(self):
        oid = "a" * 40
        store = self.store_with(("%s blob 10\n" % oid).encode() + b"short\n")
        with self.assertRaises(GitFailure):
            store.read_object(oid)

    def test_a_missing_newline_after_the_content_is_a_git_failure(self):
        content = b"hello"
        oid = hashlib.sha1(b"blob 5\0hello").hexdigest()
        store = self.store_with(("%s blob 5\n" % oid).encode() + content + b"X")
        with self.assertRaises(GitFailure):
            store.read_object(oid)

    def test_a_reply_about_another_object_is_corruption(self):
        asked, answered = "a" * 40, "b" * 40
        store = self.store_with(("%s blob 0\n\n" % answered).encode())
        with self.assertRaises(ObjectCorrupt):
            store.read_object(asked)

    def test_a_missing_reply_is_reported_as_missing(self):
        oid = "a" * 40
        store = self.store_with(("%s missing\n" % oid).encode())
        with self.assertRaises(ObjectMissing):
            store.read_object(oid)

    def test_garbage_header_and_oversized_header_are_corruption(self):
        oid = "a" * 40
        for reply in (b"garbage\n", ("%s blob xx\n" % oid).encode(), ("%s blob 99999999999\n" % oid).encode(), b""):
            with self.subTest(reply):
                store = self.store_with(reply)
                with self.assertRaises(ObjectCorrupt):
                    store.read_object(oid)
                self.assertIsNone(store._batch, "the stream was kept although its position is unknown")

    def test_reading_by_a_name_that_is_not_an_oid_is_refused_before_git_is_asked(self):
        store = self.store_with(b"")
        for name in ("HEAD", "main", "--batch", "a" * 39, ""):
            with self.assertRaises(objects.ObjectError):
                store.read_object(name)
        self.assertEqual(store._batch.stdin.getvalue(), b"")

    def test_a_real_batch_process_is_started_lazily_and_closed(self):
        self.upstream.publish({"scripts/a.py": "a\n"})
        store = ObjectStore(self.upstream.root)
        self.assertIsNone(store._batch)
        head = self.upstream.fx.git("rev-parse", "HEAD")
        store.commit_tree(head)
        proc = store._batch
        self.assertIsNotNone(proc)
        store.close()
        self.assertIsNone(store._batch)
        self.assertIsNotNone(proc.poll(), "the cat-file process was left running")


class FetchUrlTests(unittest.TestCase):
    def test_allowed_and_refused_repository_values(self):
        for ok in ("https://github.com/mikkiola/tooltempest.git", "file:///srv/repo.git", "/abs/path/repo"):
            self.assertIsNone(fetch_url_problem(ok), ok)
        for bad in ("", None, "ext::sh -c id", "--upload-pack=x", "ssh://h/x", "git://h/x", "http://h/x", "ftp://h/x",
                    "relative/repo", "file:///x\n--upload-pack=evil", "https://h/x\x00", "-https://h/x"):
            self.assertIsNotNone(fetch_url_problem(bad), repr(bad))
