import hashlib
import subprocess
import sys


def run(checker, tooling, lock, expected):
    if hashlib.sha256(open(checker, "rb").read()).hexdigest() != expected:
        raise SystemExit(1)
    return subprocess.run([sys.executable, "-B", tooling, "verify",
                           "--lock", lock],
                          capture_output=True)
