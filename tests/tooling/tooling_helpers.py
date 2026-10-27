"""Fixtures for the tooling bootstrap tests (standard library only).

Everything runs against throwaway repositories under the system temp
directory with an isolated Git environment and a throwaway HOME. No test
touches this repository's index or refs, a real consumer checkout, the
network, or ~/.claude. A fixture "ToolTempest" (Upstream) is a real Git
repository; a fixture consumer is another one; "network" in these tests is
only ever a local file:// repository.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
TOOLING_PY = SCRIPTS_DIR / "tooling.py"
RESULT_SCHEMA = REPO_ROOT / "schemas" / "tooling-result.schema.json"
RECEIPT_SCHEMA = REPO_ROOT / "schemas" / "tooling-receipt.schema.json"

sys.dont_write_bytecode = True
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from tooling_core import engine  # noqa: E402  (after the path is set up)

# Reuse the DocOps fixtures (GitFixture, clean_env, tree_digest, the mini
# JSON-schema validator) instead of copying them.
_spec = importlib.util.spec_from_file_location("docops_test_helpers", REPO_ROOT / "tests" / "docops" / "helpers.py")
docops_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(docops_helpers)
GitFixture = docops_helpers.GitFixture
clean_env = docops_helpers.clean_env
tree_digest = docops_helpers.tree_digest
validate = docops_helpers.validate

UNREACHABLE_URL = "https://tooltempest.invalid/tooltempest.git"
NO_REF = "refs/remotes/origin/none"


def sha256(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


class Upstream:
    """A fixture ToolTempest repository: MANIFEST.txt plus the files it lists."""

    def __init__(self, object_format=None):
        self.fx = GitFixture(object_format)
        self.root = self.fx.root
        self.fx.git("config", "uploadpack.allowAnySHA1InWant", "true")
        self.files = {}
        self.exec_paths = set()

    def cleanup(self):
        self.fx.cleanup()

    def publish(self, files=None, remove=(), manifest=None, executable=(), unexecutable=(),
                message="publish"):
        """Commit a new version. `files` updates the file set; the manifest lists
        every file under scripts/, schemas/, skills/, rules/ and docs/reference/
        unless given explicitly."""
        for path in remove:
            self.files.pop(path, None)
            self.fx.git("rm", "-q", "--ignore-unmatch", "-f", "--", path)
        for path, content in (files or {}).items():
            self.files[path] = content if isinstance(content, bytes) else content.encode()
            self.fx.stage(path, self.files[path])
        self.exec_paths |= set(executable)
        self.exec_paths -= set(unexecutable)
        for path in executable:
            self.fx.git("update-index", "--chmod=+x", "--", path)
        for path in unexecutable:
            self.fx.git("update-index", "--chmod=-x", "--", path)
        if manifest is None:
            listed = sorted(p for p in self.files if p.split("/", 1)[0] in
                            ("scripts", "schemas", "skills", "rules") or p.startswith("docs/reference/"))
            manifest = "\n".join(listed) + "\n"
        self.fx.stage("MANIFEST.txt", manifest)
        return self.fx.commit(message)

    def add_raw_entry(self, mode, path, oid, message="raw entry"):
        self.fx.git("update-index", "--add", "--cacheinfo", "%s,%s,%s" % (mode, oid, path))
        return self.fx.commit(message)

    def set_file_url(self, url):
        self.fx.git("config", "remote.origin.url", url)

    @property
    def url(self):
        return "file://" + self.root


class Consumer:
    """A fixture consumer repository with a .tooltempest.lock."""

    def __init__(self, object_format=None):
        self.fx = GitFixture(object_format)
        self.root = self.fx.root
        self.lock_path = os.path.join(self.root, ".tooltempest.lock")
        self.receipt_path = os.path.join(self.root, ".tooltempest.receipt.json")

    def cleanup(self):
        self.fx.cleanup()

    def write_lock(self, commit, repository=UNREACHABLE_URL, declared=None, legacy=None):
        lock = {"repository": repository, "commit": commit}
        if legacy:
            lock["path"], lock["sha256"] = legacy
        if declared:
            lock["files"] = [{"path": p, "sha256": h} for p, h in sorted(declared.items())]
        with open(self.lock_path, "w", encoding="utf-8") as handle:
            json.dump(lock, handle, indent=2)
            handle.write("\n")

    def path(self, rel):
        return os.path.join(self.root, rel)

    def read(self, rel):
        with open(self.path(rel), "rb") as handle:
            return handle.read()

    def exists(self, rel):
        return os.path.lexists(self.path(rel))

    def write(self, rel, content, mode=None):
        path = self.path(rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(content if isinstance(content, bytes) else content.encode())
        if mode is not None:
            os.chmod(path, mode)

    def mode(self, rel):
        return stat.S_IMODE(os.lstat(self.path(rel)).st_mode)

    def receipt(self):
        with open(self.receipt_path, "rb") as handle:
            return json.loads(handle.read())


def tool_env(home, extra=None):
    env = clean_env({"HOME": home})
    if extra:
        env.update(extra)
    return env


def run_tooling(args, home, cwd=None, env=None, umask=None):
    preexec = (lambda: os.umask(umask)) if umask is not None else None
    proc = subprocess.run([sys.executable, "-I", str(TOOLING_PY), *args], cwd=cwd,
                          capture_output=True, env=env or tool_env(home), check=False,
                          stdin=subprocess.DEVNULL, preexec_fn=preexec)
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


def load_schema(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class ToolingCase(unittest.TestCase):
    """Base: a throwaway HOME, an Upstream, a Consumer, helpers to run the CLI."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="tooling-home-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.upstream = Upstream()
        self.addCleanup(self.upstream.cleanup)
        self.consumer = Consumer()
        self.addCleanup(self.consumer.cleanup)

    # -- running --------------------------------------------------------------
    def engine_options(self, **kwargs):
        """Options for calling the engine in-process (to inject failures)."""
        params = dict(root=self.consumer.root, lock_path=self.consumer.lock_path,
                      receipt_path=self.consumer.receipt_path, source=self.upstream.root,
                      invoked_as="tooling.py")
        params.update(kwargs)
        return engine.Options(**params)

    def args(self, command, *extra, source=None, root=None):
        return [command, "--root", root or self.consumer.root,
                "--source", source or self.upstream.root, "--format", "json", *extra]

    def run_json(self, command, *extra, umask=None, **kwargs):
        code, out, err = run_tooling(self.args(command, *extra, **kwargs), self.home, umask=umask)
        try:
            doc = json.loads(out)
        except ValueError:
            self.fail("not JSON (exit %d): %r %r" % (code, out[:300], err[:300]))
        self.assertEqual(code, doc["summary"]["exit_code"], "exit code and summary disagree")
        errors = validate(doc, load_schema(RESULT_SCHEMA))
        self.assertEqual(errors, [], "result does not conform to the schema: %s" % errors[:5])
        return code, doc

    def verify(self, *extra, **kwargs):
        return self.run_json("verify", *extra, **kwargs)

    def materialize(self, *extra, **kwargs):
        return self.run_json("materialize", *extra, **kwargs)

    def apply(self):
        code, doc = self.materialize("--apply")
        self.assertEqual(code, 0, doc["findings"])
        return doc

    # -- assertions -----------------------------------------------------------------
    def codes(self, doc, severity=None):
        return sorted(f["code"] for f in doc["findings"] if severity in (None, f["severity"]))

    def file(self, doc, path):
        for record in doc["files"]:
            if record["path"] == path:
                return record
        self.fail("no file record for %s" % path)

    def assertReceiptValid(self):
        errors = validate(self.consumer.receipt(), load_schema(RECEIPT_SCHEMA))
        self.assertEqual(errors, [], errors[:5])

    def digest(self, *roots):
        return [tree_digest(r) for r in roots]

    # -- standard content -----------------------------------------------------------------
    def publish_v1(self, **kwargs):
        files = {
            "scripts/alpha.py": "print('alpha v1')\n",
            "scripts/run.sh": "#!/bin/sh\necho v1\n",
            "schemas/beta.schema.json": '{"v": 1}\n',
            "skills/demo/SKILL.md": "# skill v1\n",
            "docs/reference/rules.md": "# rules\n",
        }
        files.update(kwargs.pop("files", {}))
        executable = kwargs.pop("executable", ("scripts/run.sh",))
        self.commit1 = self.upstream.publish(files, executable=executable, message="v1", **kwargs)
        self.consumer.write_lock(self.commit1)
        return self.commit1

    def publish_v2(self):
        self.commit2 = self.upstream.publish({"scripts/alpha.py": "print('alpha v2')\n",
                                              "schemas/gamma.schema.json": '{"v": 2}\n'},
                                             executable=("scripts/run.sh",), message="v2")
        return self.commit2
