import hashlib
import subprocess
import sys


def digest(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def run(checker, other, expected):
    if digest(checker) != expected:
        raise SystemExit(1)
    return subprocess.run([sys.executable, "-B", other, "check"], capture_output=True)
