import hashlib
import subprocess
import sys


def run(checker, tool, expected):
    if hashlib.sha256(open(checker, "rb").read()).hexdigest() != expected:
        raise SystemExit(1)
    return subprocess.run([sys.executable,  # the interpreter
                           "-B",  # no bytecode
                           tool])
