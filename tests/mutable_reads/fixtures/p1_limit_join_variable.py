# Documents a form the scan does not cover (see `--limits`): the home part of a clone path is held in a variable
# that the scan does not know, so the joined path is not recognized as a clone path.
import os

base = os.path.expanduser("~")
rules = os.path.join(base, "Dev", "tooltempest", "docs", "reference", "rules.md")
