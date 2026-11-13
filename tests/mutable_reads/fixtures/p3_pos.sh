#!/bin/bash
resolve_checker() { echo "$REPO_ROOT/scripts/docops.py"; }
CHECKER="$(resolve_checker)"
python3 "$CHECKER" check --tree index
