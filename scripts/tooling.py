#!/usr/bin/env python3
"""ToolTempest tooling bootstrap: reproduce a consumer's repository-local dependencies.

    tooling.py verify      [--root DIR] [--lock FILE] [--receipt FILE] [--source DIR] [--format text|json]
    tooling.py status      (same options; informational, exit 0 unless the tool fails)
    tooling.py materialize (same options) [--apply] [--allow-network]

`verify` and `status` are read-only and never touch the network. They report
local integrity, upstream provenance and freshness separately. `materialize`
is a dry run unless `--apply` is given, never overwrites a file it does not
manage, never repins, never touches ~/.claude, and reaches the network only
with `--allow-network` and only when the pinned commit is absent locally.
Exit codes: 0 ok, 1 findings (or refused / nothing applied), 2 the tool
could not run. See docs/adr/0015 and schemas/tooling-result.schema.json.
"""
import sys

sys.dont_write_bytecode = True

import json
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from tooling_core.cli import main
except Exception as exc:  # missing or broken package: never look like success
    sys.stdout.write(json.dumps({
        "schema_version": 1,
        "tool": {"name": "tooltempest-tooling", "version": None},
        "command": None,
        "summary": {"exit_code": 2},
        "error": {"code": "IMPORT_FAILED",
                  "message": "tooling_core could not be imported: %s: %s" % (type(exc).__name__, exc)},
    }) + "\n")
    sys.exit(2)

if __name__ == "__main__":
    sys.exit(main())
