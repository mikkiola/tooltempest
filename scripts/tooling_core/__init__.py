"""ToolTempest tooling bootstrap core (standard library only).

Behind scripts/tooling.py: verified Git-object access, the lock, manifest and
install-receipt formats, and the verify / status / materialize engine.

This package is separate from docops_core on purpose (two command surfaces:
the checker reads a snapshot and judges documents; tooling reproduces a
consumer's repository-local dependencies). It never touches ~/.claude.
"""

TOOL_NAME = "tooltempest-tooling"
TOOL_VERSION = "0.1.0"
RESULT_SCHEMA_VERSION = 1
RECEIPT_SCHEMA_VERSION = 1
