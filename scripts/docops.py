#!/usr/bin/env python3
"""DocOps checker: read-only SPEC-state check of an exact Git snapshot.

    docops.py check  --tree index|REV [--tree ...] [--push-stdin] [--impact-file PATH|-] [--repo PATH]
    docops.py report --tree index|REV [--tree ...] [--push-stdin] [--impact-file PATH|-] [--repo PATH]

`check` prints a versioned JSON result, `report` the same result as
text. Exit 0 only when every evaluated check is PASS or N/A; 1 on a
violation, or when a supplied impact report's required assessment is not
complete; 2 when the checker could not run to a verdict (usage,
dependency, I/O, Git, policy, internal error: never a per-check status; a
usage error and a failure to render the output are printed on stderr
only, a failure to write it leaves no result at all, any other failure
also appears in the JSON result as an `error` object or `summary.reason`); 3 when SPEC applicability cannot be
established (1 takes precedence when both apply). See docs/adr/0013,
docs/adr/0014 and schemas/docops-result.schema.json.

The checker writes nothing: no files (bytecode caching is disabled
below), no index, config, refs or objects, no network. One limit: when it
reads the index (`--tree index`), Git itself runs the program named by the
repository's own `core.fsmonitor` setting, if any; the checker neither
starts nor disables it.
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
