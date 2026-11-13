#!/bin/bash
common="$(cd "$(git rev-parse --git-common-dir)" && pwd)"
for candidate in "$REPO_ROOT/scripts/docops.py" "$(dirname "$common")/scripts/docops.py"; do
  [ -f "$candidate" ] && echo "$candidate" && break
done
