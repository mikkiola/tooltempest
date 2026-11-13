#!/usr/bin/env bash
# Shape of the second incident: the checker is looked up in this worktree, then in the main worktree.
resolve_docops_checker() {
  local common candidate
  common="$(cd "$(git rev-parse --git-common-dir)" && pwd)"
  for candidate in "$REPO_ROOT/scripts/docops.py" "$(dirname "$common")/scripts/docops.py"; do
    if [ -f "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

if ! DOCOPS_CHECKER="$(resolve_docops_checker)"; then
  echo "checker not found; commit blocked."
  exit 1
fi
python3 "$DOCOPS_CHECKER" check --repo "$REPO_ROOT" --tree index
