import subprocess
import sys


def run(resolve_checker):
    checker = resolve_checker()
    return subprocess.run([sys.executable, checker, "check"], check=False)
