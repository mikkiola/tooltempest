import hashlib


def proves(received: bytes, blob: bytes) -> bool:
    return hashlib.sha256(received).hexdigest() == hashlib.sha256(blob).hexdigest()
