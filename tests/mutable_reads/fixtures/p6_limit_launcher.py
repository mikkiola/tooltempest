# Documents a form the scan does not cover (see `--limits`): the file is run through a launcher, so P6 does not see it.
import hashlib
import subprocess


def run(checker, expected):
    if hashlib.sha256(open(checker, "rb").read()).hexdigest() != expected:
        raise SystemExit(1)
    return subprocess.run(["uv", "run", "scripts/other.py"])
