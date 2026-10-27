"""DocOps shared checker core (standard library only).

Pure, read-only building blocks behind scripts/docops.py: Git snapshot
access, SPEC parsing, the SPEC-state policy, and the versioned result
format. Nothing in this package writes files, stages, commits, pushes,
or touches the network.
"""

TOOL_NAME = "docops"
TOOL_VERSION = "0.2.0"
RESULT_SCHEMA_VERSION = 1
