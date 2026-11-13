import hashlib
import os

VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", "objects.py")
EXPECTED = "0" * 64


def load():
    data = open(VENDOR, "rb").read()
    if hashlib.sha256(data).hexdigest() != EXPECTED:
        raise SystemExit(2)
    namespace = {}
    exec(compile(data, VENDOR, "exec"), namespace)
    return namespace
