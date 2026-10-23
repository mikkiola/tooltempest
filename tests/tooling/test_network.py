"""Network discipline: offline unless the user said otherwise, and then only
for the one pinned commit, only when it is genuinely absent.

"The network" in these tests is a local file:// repository; no test reaches
a real host. The proof that nothing was fetched is that the object stays
absent from the source afterwards.
"""
import os
import subprocess
from unittest import mock

from tooling_helpers import ToolingCase, clean_env, engine


def has_object(repo, oid):
    return subprocess.run(["git", "-C", repo, "cat-file", "-e", oid], env=clean_env(),
                          capture_output=True).returncode == 0


class OfflineTests(ToolingCase):
    def test_a_cached_object_installs_with_an_unreachable_remote_and_no_network_use(self):
        self.publish_v1()  # the lock names an unreachable repository
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 0)
        self.assertEqual(doc["network"], {"allowed": False, "used": False})
        self.assertEqual(self.verify()[0], 0)

    def test_allowing_the_network_does_not_use_it_when_the_object_is_present(self):
        self.publish_v1()
        with mock.patch.object(engine, "fetch_commit", side_effect=AssertionError("fetched")):
            doc = engine.run_materialize(self.engine_options(apply=True, allow_network=True))
        self.assertEqual(doc["summary"]["exit_code"], 0, doc["findings"])
        self.assertEqual(doc["network"], {"allowed": True, "used": False})

    def test_a_missing_object_without_permission_fails_clearly_and_fetches_nothing(self):
        self.publish_v1()
        commit2 = self.publish_v2()
        empty = os.path.join(self.home, "empty")
        subprocess.run(["git", "init", "-q", empty], env=clean_env(), check=True)
        self.consumer.write_lock(commit2, repository=self.upstream.url)  # a remote that WOULD serve it
        with mock.patch.object(engine, "fetch_commit", side_effect=AssertionError("fetched")):
            doc = engine.run_materialize(self.engine_options(apply=True, source=empty))
        self.assertEqual(doc["summary"]["exit_code"], 1)
        code, doc = self.materialize("--apply", source=empty)  # the real process, too
        self.assertEqual(code, 1)
        finding = [f for f in doc["findings"] if f["code"] == "PIN_OBJECT_MISSING"][0]
        self.assertIn("nothing was fetched", finding["hint"])
        self.assertIn("--allow-network", finding["hint"])
        self.assertFalse(doc["network"]["used"])
        self.assertFalse(has_object(empty, commit2), "the object appeared in the source: something fetched")
        self.assertFalse(self.consumer.exists("scripts/alpha.py"))
        self.assertFalse(self.consumer.exists(".tooltempest.receipt.json"))


class ExplicitNetworkTests(ToolingCase):
    def lock_to_upstream(self):
        self.publish_v1()
        self.consumer.write_lock(self.commit1, repository=self.upstream.url)

    def test_with_permission_exactly_the_pinned_commit_is_fetched_into_a_new_cache(self):
        self.lock_to_upstream()
        cache = os.path.join(self.home, "new-cache", "repo")
        self.assertFalse(os.path.exists(cache))
        code, doc = self.materialize("--apply", "--allow-network", source=cache)
        self.assertEqual(code, 0, doc["findings"])
        self.assertEqual(doc["network"], {"allowed": True, "used": True})
        self.assertTrue(has_object(cache, self.commit1))
        self.assertEqual(self.consumer.read("scripts/alpha.py"), self.upstream.files["scripts/alpha.py"])
        ref = subprocess.run(["git", "-C", cache, "for-each-ref", "--format=%(refname)"], env=clean_env(),
                             capture_output=True).stdout.decode().split()
        self.assertEqual(ref, ["refs/tooltempest/pins/" + self.commit1])  # no branch, no tag
        # afterwards everything is offline again
        self.assertEqual(self.verify(source=cache)[0], 0)

    def test_the_fetch_is_for_the_pin_only_not_for_a_newer_commit(self):
        self.lock_to_upstream()
        commit2 = self.publish_v2()
        cache = os.path.join(self.home, "cache")
        code, doc = self.materialize("--apply", "--allow-network", source=cache)
        self.assertEqual(code, 0)
        self.assertTrue(has_object(cache, self.commit1))
        self.assertFalse(has_object(cache, commit2), "a commit that is not the pin was fetched")

    def test_a_stale_existing_cache_is_topped_up_not_replaced(self):
        self.lock_to_upstream()
        cache = os.path.join(self.home, "stale")
        subprocess.run(["git", "clone", "-q", "--no-local", "--branch", "main", self.upstream.root, cache],
                       env=clean_env(), check=True, capture_output=True)
        subprocess.run(["git", "-C", cache, "branch", "-q", "keepme"], env=clean_env(), check=True)
        commit2 = self.publish_v2()  # published after the cache was made
        self.consumer.write_lock(commit2, repository=self.upstream.url)
        self.assertFalse(has_object(cache, commit2))
        code, doc = self.materialize("--apply", "--allow-network", source=cache)
        self.assertEqual(code, 0, doc["findings"])
        self.assertTrue(has_object(cache, commit2))
        heads = subprocess.run(["git", "-C", cache, "branch", "--list"], env=clean_env(),
                               capture_output=True).stdout.decode()
        self.assertIn("keepme", heads)
        self.assertEqual(self.consumer.read("scripts/alpha.py"), b"print('alpha v2')\n")

    def test_a_failed_fetch_is_reported_and_leaves_the_checkout_alone(self):
        self.publish_v1()
        self.consumer.write_lock(self.commit1, repository="file://" + os.path.join(self.home, "no-such-remote"))
        cache = os.path.join(self.home, "cache")
        before = self.digest(self.consumer.root)
        code, doc = self.materialize("--apply", "--allow-network", source=cache)
        self.assertEqual(code, 1)
        self.assertIn("FETCH_FAILED", self.codes(doc, "error"))
        self.assertNotIn("PIN_OBJECT_MISSING", self.codes(doc))
        self.assertTrue(doc["network"]["used"])
        self.assertEqual(self.digest(self.consumer.root), before)

    def test_repository_values_that_could_run_code_or_name_other_transports_are_never_fetched(self):
        self.publish_v1()
        marker = os.path.join(self.home, "pwned")
        for url in ("ext::sh -c 'touch %s'" % marker, "--upload-pack=touch %s" % marker,
                    "ftp://example.invalid/x.git", "relative/path.git", "http://example.invalid/x.git",
                    "git://example.invalid/x.git", "ssh://example.invalid/x.git",
                    "file:///tmp/x\n--upload-pack=touch %s" % marker):
            with self.subTest(url):
                self.consumer.write_lock(self.commit1, repository=url)
                cache = os.path.join(self.home, "cache-" + str(abs(hash(url))))
                code, doc = self.materialize("--apply", "--allow-network", source=cache)
                self.assertEqual(code, 1)
                self.assertIn("FETCH_REFUSED", self.codes(doc, "error"))
                self.assertFalse(doc["network"]["used"])
                self.assertFalse(os.path.exists(marker))
                self.assertFalse(os.path.exists(cache), "a cache was created for a refused URL")

    def test_a_fetched_commit_is_still_verified_before_any_byte_is_used(self):
        # the object store is trusted for nothing: a lock that pins a commit the remote does not have
        self.publish_v1()
        self.consumer.write_lock("1" * 40, repository=self.upstream.url)
        cache = os.path.join(self.home, "cache")
        code, doc = self.materialize("--apply", "--allow-network", source=cache)
        self.assertEqual(code, 1)
        self.assertIn("FETCH_FAILED", self.codes(doc, "error"))
        self.assertFalse(self.consumer.exists("scripts/alpha.py"))


class RepositoryValueTests(ToolingCase):
    def test_a_local_absolute_path_is_an_allowed_repository_value(self):
        self.publish_v1()
        self.consumer.write_lock(self.commit1, repository=self.upstream.root)
        cache = os.path.join(self.home, "cache")
        code, doc = self.materialize("--apply", "--allow-network", source=cache)
        self.assertEqual(code, 0, doc["findings"])
        self.assertTrue(doc["network"]["used"])

    def test_sha256_repositories_can_be_fetched_into_a_new_cache(self):
        from tooling_helpers import Consumer, Upstream
        self.upstream.cleanup()
        self.consumer.cleanup()
        self.upstream = Upstream("sha256")
        self.consumer = Consumer("sha256")
        self.addCleanup(self.upstream.cleanup)
        self.addCleanup(self.consumer.cleanup)
        self.publish_v1()
        self.consumer.write_lock(self.commit1, repository=self.upstream.url)
        cache = os.path.join(self.home, "cache256")
        code, doc = self.materialize("--apply", "--allow-network", source=cache)
        self.assertEqual(code, 0, doc["findings"])
        self.assertEqual(len(doc["pin"]["commit"]), 64)
        self.assertEqual(self.verify(source=cache)[0], 0)


class DryRunNeverFetchesTests(ToolingCase):
    def test_allow_network_without_apply_fetches_nothing(self):
        self.publish_v1()
        self.consumer.write_lock(self.commit1, repository=self.upstream.url)
        cache = os.path.join(self.home, "cache")
        with mock.patch.object(engine, "fetch_commit", side_effect=AssertionError("fetched")):
            doc = engine.run_materialize(self.engine_options(allow_network=True, source=cache))
        self.assertEqual(doc["summary"]["exit_code"], 1)
        self.assertFalse(doc["network"]["used"])
        self.assertFalse(os.path.exists(cache), "a dry run created the cache")
        hint = [f for f in doc["findings"] if f["code"] == "SOURCE_UNAVAILABLE"][0]["hint"]
        self.assertIn("--apply --allow-network", hint)


class FailedFetchIntoExistingCacheTests(ToolingCase):
    def test_a_failed_fetch_into_an_existing_cache_is_one_diagnosis(self):
        self.publish_v1()
        empty = os.path.join(self.home, "empty-cache")
        subprocess.run(["git", "init", "-q", empty], env=clean_env(), check=True)
        self.consumer.write_lock(self.commit1, repository="file://" + os.path.join(self.home, "no-such-remote"))
        code, doc = self.materialize("--apply", "--allow-network", source=empty)
        self.assertEqual(code, 1)
        self.assertEqual(self.codes(doc, "error"), ["FETCH_FAILED"])
