# Documents a form the scan does not cover (see `--limits`): a comma inside a quoted element splits the list wrongly.
import hashlib
import subprocess
import sys


def run(checker, tool, expected):
    if hashlib.sha256(open(checker, "rb").read()).hexdigest() != expected:
        raise SystemExit(1)
    return subprocess.run([sys.executable, "-W", "ignore,error", tool])
