"""ToolTempest tooling bootstrap core (standard library only).

Behind scripts/tooling.py: verified Git-object access, the lock, manifest and
install-receipt formats, and the verify / status / materialize engine.

This package is separate from docops_core on purpose (two command surfaces:
the checker reads a snapshot and judges documents; tooling reproduces a
consumer's repository-local dependencies). It touches nothing outside the root it was given,
and never anything under a client directory unless the client target is invoked explicitly.
"""

TOOL_NAME = "tooltempest-tooling"
TOOL_VERSION = "0.2.0"
RESULT_SCHEMA_VERSION = 1
RECEIPT_SCHEMA_VERSION = 1  # the lowest version; version 2 adds the contract and client roots (ADR-0017)
RECEIPT_SCHEMA_VERSION_2 = 2
SUPPORTED_RECEIPT_VERSIONS = (RECEIPT_SCHEMA_VERSION, RECEIPT_SCHEMA_VERSION_2)
