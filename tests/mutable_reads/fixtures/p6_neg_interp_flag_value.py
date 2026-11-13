import hashlib
import subprocess
import sys


def run(tool, expected):
    if hashlib.sha256(open(tool, "rb").read()).hexdigest() != expected:
        raise SystemExit(1)
    return subprocess.check_output([sys.executable, "-X", "utf8", "-B", tool, "verify"])
