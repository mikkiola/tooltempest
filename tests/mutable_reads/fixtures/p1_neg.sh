#!/bin/bash
CLONE="$HOME/.cache/tooltempest/repo"
git -C "$CLONE" cat-file blob "$PIN:docs/tooltempest/universal-rules.md"
echo "the source is $CLONE/docs/tooltempest"
