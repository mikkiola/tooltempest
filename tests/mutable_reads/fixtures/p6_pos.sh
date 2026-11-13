#!/bin/bash
EXPECTED="$(python3 -c 'import json;print(json.load(open(".tooltempest.lock"))["sha256"])')"
ACTUAL="$(shasum -a 256 "$REPO_ROOT/scripts/docops.py" | cut -c1-64)"
[ "$ACTUAL" = "$EXPECTED" ] || exit 1
python3 "$OTHER_CHECKER" check --tree index
