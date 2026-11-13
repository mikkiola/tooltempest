#!/bin/bash
resolve_checker() { echo "$REPO_ROOT/scripts/docops.py"; }
CHECKER="$(resolve_checker)"
ACTUAL="$(shasum -a 256 "$CHECKER" | cut -c1-64)"
[ "$ACTUAL" = "$EXPECTED" ] || exit 1
python3 "$CHECKER" check --tree index
