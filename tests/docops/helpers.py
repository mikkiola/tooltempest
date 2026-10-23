"""Shared fixtures for the DocOps checker tests (standard library only).

Every fixture repository is a throwaway directory with an isolated Git
environment (no user/system config, no hooks, no signing). Nothing here
touches the tooltempest repository itself or any consumer repository.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
DOCOPS_PY = SCRIPTS_DIR / "docops.py"
RESULT_SCHEMA = REPO_ROOT / "schemas" / "docops-result.schema.json"
IMPACT_SCHEMA = REPO_ROOT / "schemas" / "docops-impact-report.schema.json"

sys.dont_write_bytecode = True
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

GOOD_SPEC = """\
# Feature X — Specification

## Goals

- [ ] Goal checkbox that is not a milestone
- [x] Another goal checkbox

## Milestones

- [ ] M1: first thing
- [x] M2: second thing

## Open Questions

- [ ] A question checkbox
"""

IDLE_SPEC = """\
---
spec-state: idle
---

# SPEC.md

No active task right now.
"""


def clean_env(extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    home = tempfile.gettempdir()
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "HOME": home,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    if extra:
        env.update(extra)
    return env


class GitFixture:
    """A scratch repository. Use as a context manager or call cleanup()."""

    def __init__(self, object_format=None):
        self._tmp = tempfile.mkdtemp(prefix="docops-test-")
        self.root = os.path.realpath(os.path.join(self._tmp, "repo"))
        os.mkdir(self.root)
        extra = ["--object-format=" + object_format] if object_format else []
        try:
            self.git("init", "-q", "-b", "main", *extra)
        except AssertionError:
            self.cleanup()
            raise
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.hooksPath", os.devnull)

    def cleanup(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.cleanup()

    def git(self, *args, check=True, input_bytes=None):
        proc = subprocess.run(
            ["git", "-C", self.root, *args], capture_output=True, env=clean_env(),
            input=input_bytes, check=False,
        )
        if check and proc.returncode != 0:
            raise AssertionError("git %s failed: %s" % (args, proc.stderr.decode()))
        return proc.stdout.decode("utf-8", "replace").strip()

    def write(self, rel, content):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = content if isinstance(content, bytes) else content.encode("utf-8")
        with open(path, "wb") as handle:
            handle.write(data)

    def stage(self, rel, content):
        self.write(rel, content)
        self.git("add", "--", rel)

    def commit(self, message="c"):
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def commit_files(self, files, message="c"):
        for rel, content in files.items():
            self.stage(rel, content)
        return self.commit(message)


def tree_digest(root):
    """Digest of every file under root (relative path, size, mtime_ns, content)."""
    digest = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            stat = os.lstat(path)
            digest.update(("%s|%d|%d|" % (rel, stat.st_size, stat.st_mtime_ns)).encode())
            if os.path.islink(path):
                digest.update(os.readlink(path).encode())
            else:
                with open(path, "rb") as handle:
                    digest.update(handle.read())
        for name in dirnames:
            digest.update(("dir|%s|" % os.path.relpath(os.path.join(dirpath, name), root)).encode())
    return digest.hexdigest()


def run_cli(args, cwd=None, stdin=b"", env=None, script=None):
    """Run scripts/docops.py in isolated mode; return (exit, stdout, stderr)."""
    proc = subprocess.run(
        [sys.executable, "-I", str(script or DOCOPS_PY), *args],
        cwd=cwd, input=stdin, capture_output=True, env=env or clean_env(), check=False,
    )
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


def run_check_json(args, **kwargs):
    code, out, err = run_cli(["check", *args], **kwargs)
    return code, json.loads(out), err


# --- minimal JSON Schema validator (type/enum/const/required/properties/
# additionalProperties/items/$ref/anyOf/allOf/not/if-then-else/minimum/
# minItems/minLength/pattern), enough to prove result and report conformance
# without a third-party dependency --------------------------------------

_TYPES = {
    "object": dict, "array": list, "string": str, "integer": int,
    "boolean": bool, "null": type(None),
}


def _is_type(value, name):
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, _TYPES[name])


def _same(a, b):
    """Equality that does not conflate True/1 or False/0."""
    return isinstance(a, bool) == isinstance(b, bool) and a == b


def validate(instance, schema, root=None, path="$"):
    """Return a list of violation strings (empty = valid)."""
    root = root or schema
    errors = []
    if "$ref" in schema:
        target = root
        for part in schema["$ref"].lstrip("#/").split("/"):
            target = target[part]
        errors += validate(instance, target, root, path)
    if "anyOf" in schema and not any(not validate(instance, option, root, path)
                                     for option in schema["anyOf"]):
        errors.append("%s: %r matches none of the anyOf alternatives" % (path, instance))
    for part in schema.get("allOf", []):
        errors += validate(instance, part, root, path)
    if "not" in schema and not validate(instance, schema["not"], root, path):
        errors.append("%s: %r matches a forbidden alternative" % (path, instance))
    if "if" in schema:
        branch = "then" if not validate(instance, schema["if"], root, path) else "else"
        if branch in schema:
            errors += validate(instance, schema[branch], root, path)
    if "const" in schema and not _same(instance, schema["const"]):
        errors.append("%s: expected const %r, got %r" % (path, schema["const"], instance))
    if "enum" in schema and not any(_same(instance, option) for option in schema["enum"]):
        errors.append("%s: %r not in enum %r" % (path, instance, schema["enum"]))
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(instance, t) for t in types):
            errors.append("%s: %r is not of type %r" % (path, instance, types))
            return errors
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append("%s: missing required %r" % (path, key))
        props = schema.get("properties", {})
        for key, value in instance.items():
            if key in props:
                errors += validate(value, props[key], root, "%s.%s" % (path, key))
            elif schema.get("additionalProperties") is False:
                errors.append("%s: unexpected property %r" % (path, key))
    if isinstance(instance, list) and "minItems" in schema and len(instance) < schema["minItems"]:
        errors.append("%s: fewer than %d items" % (path, schema["minItems"]))
    if isinstance(instance, list) and "items" in schema:
        for index, item in enumerate(instance):
            errors += validate(item, schema["items"], root, "%s[%d]" % (path, index))
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append("%s: shorter than minLength" % path)
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append("%s: does not match %s" % (path, schema["pattern"]))
    if isinstance(instance, int) and not isinstance(instance, bool) and "minimum" in schema:
        if instance < schema["minimum"]:
            errors.append("%s: below minimum" % path)
    return errors


def load_result_schema():
    return json.loads(RESULT_SCHEMA.read_text(encoding="utf-8"))


def load_impact_schema():
    return json.loads(IMPACT_SCHEMA.read_text(encoding="utf-8"))


class DocopsTestCase(unittest.TestCase):
    def assertValidResult(self, result):
        errors = validate(result, load_result_schema())
        self.assertEqual(errors, [], "result does not conform to schema: %s" % errors[:5])

    def assertNeverPassOrNa(self, status):
        self.assertNotIn(status, ("PASS", "N/A"), "failure state reported as %s" % status)

    def assertToolError(self, result, code=None):
        """A snapshot the checker could not evaluate: no checks, no status, exit 2."""
        self.assertIsNotNone(result["error"], result)
        self.assertIsNone(result["checks"])
        self.assertIsNone(result["status"])
        self.assertEqual(result["exit_code"], 2)
        if code is not None:
            self.assertEqual(result["error"]["code"], code)
