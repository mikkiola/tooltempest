"""Shape of the first incident: the path of a clone is a constant, and the read is judged by its path."""
import os
from pathlib import Path

WORKSPACE = Path.home() / "work" / "org"
RULES = WORKSPACE / "tooltempest" / "docs" / "reference" / "documentation-rules.md"


def _real(p):
    return os.path.realpath(p)


def _is_full_read(tin):
    if not isinstance(tin, dict) or _real(tin.get("file_path") or "") != _real(RULES):
        return False
    with open(RULES, "rb") as f:
        total = sum(1 for _ in f)
    return tin.get("limit") is None or tin["limit"] >= total
