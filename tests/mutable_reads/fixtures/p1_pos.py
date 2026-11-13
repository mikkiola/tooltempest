from pathlib import Path

WORKSPACE = Path.home() / "work" / "org"
RULES = WORKSPACE / "tooltempest" / "docs" / "reference" / "rules.md"


def load_rules():
    return RULES.read_text()
