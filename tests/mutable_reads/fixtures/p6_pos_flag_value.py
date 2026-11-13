import hashlib
import subprocess


def run(checker, tool, expected):
    if hashlib.sha256(open(checker, "rb").read()).hexdigest() != expected:
        raise SystemExit(1)
    return subprocess.check_output(["/usr/bin/python3", "-X", "utf8", tool, "verify"])
