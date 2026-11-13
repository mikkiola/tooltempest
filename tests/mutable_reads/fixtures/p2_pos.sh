#!/bin/bash
CLONE="$HOME/.cache/tooltempest/repo"
cd "$CLONE" && python3 scripts/docops.py check --tree index
cd ~/.cache/tooltempest/repo
cat docs/tooltempest/universal-rules.md
