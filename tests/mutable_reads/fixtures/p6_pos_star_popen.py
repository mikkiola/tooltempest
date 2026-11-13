import hashlib
import subprocess
import sys


def run(checker, cmd, expected):
    if hashlib.sha256(open(checker, "rb").read()).hexdigest() != expected:
        raise SystemExit(1)
    return subprocess.Popen([sys.executable, *cmd], stdout=subprocess.PIPE)
