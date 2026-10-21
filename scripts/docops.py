#!/usr/bin/env python3
"""DocOps checker: read-only SPEC-state check of an exact Git snapshot.

    docops.py check  --tree index|REV [--tree ...] [--push-stdin] [--repo PATH]
    docops.py report --tree index|REV [--tree ...] [--push-stdin] [--repo PATH]

`check` prints a versioned JSON result, `report` the same result as
text. Exit 0 only when every evaluated check is PASS or N/A; 1 on a
violation, 2 when the checker itself could not run to a verdict (usage,
dependency, Git, policy, internal error; reported via the exit code and
an `error` object, never as a per-check status), 3 when applicability
cannot be established. See docs/adr/0013 and
schemas/docops-result.schema.json.

The checker writes nothing: no files (bytecode caching is disabled
below), no index, config, refs or objects, no network.
"""
import sys

sys.dont_write_bytecode = True

import json
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from docops_core.cli import main
except Exception as exc:  # missing or broken package: never look like a pass
    sys.stdout.write(json.dumps({
        "schema_version": 1,
        "tool": {"name": "docops", "version": None},
        "command": None,
        "repository": {"root": None},
        "snapshots": [],
        "summary": {
            "status": None,
            "exit_code": 2,
            "tool_errors": 1,
            "reason": "docops_core could not be imported: %s: %s" % (type(exc).__name__, exc),
        },
    }) + "\n")
    sys.exit(2)

if __name__ == "__main__":
    sys.exit(main())
