"""Fixtures for the distribution tests (standard library only).

These tests qualify what ToolTempest hands to consumers: the manifest check,
the import surface the legacy scripts share, and the file set `tooling.py`
materializes for the exact candidate commit. Everything that writes runs in
throwaway directories under the system temp directory with an isolated Git
environment and HOME pointed at a temporary directory. The real checkout is only ever read (its
Git objects, as the materialization source); no consumer repository, home
directory or global tool directory is touched, and nothing uses the network.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"

sys.dont_write_bytecode = True
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Reuse the DocOps fixtures (GitFixture, clean_env, the mini JSON-schema validator).
_spec = importlib.util.spec_from_file_location("docops_test_helpers", REPO_ROOT / "tests" / "docops" / "helpers.py")
docops_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(docops_helpers)
GitFixture = docops_helpers.GitFixture
clean_env = docops_helpers.clean_env
validate = docops_helpers.validate

COMPOSITION_DIRS = ("scripts/", "schemas/", "skills/", "rules/", "docs/reference/")


def git_out(*args, cwd=REPO_ROOT, check=True):
    """Run Git read-only in `cwd` with an isolated environment; return stdout bytes."""
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, env=clean_env(), check=False)
    if check and proc.returncode != 0:
        raise AssertionError("git %s failed: %s" % (args, proc.stderr.decode()))
    return proc.stdout


def candidate_commit():
    """The exact commit the candidate checkout is at: the source of the distribution."""
    return git_out("rev-parse", "--verify", "HEAD^{commit}").decode().strip()


def run_python(args, cwd, extra_env=None):
    """Run the current interpreter in isolated mode; return (exit code, stdout, stderr)."""
    proc = subprocess.run([sys.executable, "-I", *map(str, args)], cwd=str(cwd), capture_output=True,
                          env=clean_env(extra_env), check=False, stdin=subprocess.DEVNULL)
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


class TempDirCase(unittest.TestCase):
    def tempdir(self):
        import shutil
        path = tempfile.mkdtemp(prefix="dist-test-")
        self.addCleanup(shutil.rmtree, path, ignore_errors=True)
        return Path(path).resolve()
