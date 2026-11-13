#!/bin/bash
resolve_checker() { echo "$REPO_ROOT/scripts/docops.py"; }
python3 "$(resolve_checker)" check --tree index
