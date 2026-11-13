#!/bin/bash
CLONE="$HOME/.cache/tooltempest/repo"
cd "$REPO_ROOT"
python3 scripts/docops.py check --tree index
git -C "$CLONE" rev-parse HEAD
