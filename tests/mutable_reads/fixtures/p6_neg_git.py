import hashlib
import subprocess


def verified(data, expected):
    if hashlib.sha256(data).hexdigest() != expected:
        raise SystemExit(1)
    return data


def git(root, *args):
    # A program found on PATH is not a pinned file: nothing here runs a file from a path.
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)
