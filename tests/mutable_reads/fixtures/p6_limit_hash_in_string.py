# Documents a form the scan does not cover (see `--limits`): a "#" that follows a space inside a quoted element
# is read as the start of a comment, so the list is cut short and the file it runs is not seen.
import hashlib
import subprocess
import sys


def run(checker, tool, expected):
    if hashlib.sha256(open(checker, "rb").read()).hexdigest() != expected:
        raise SystemExit(1)
    return subprocess.run([sys.executable, "-W", "x #y", tool])
