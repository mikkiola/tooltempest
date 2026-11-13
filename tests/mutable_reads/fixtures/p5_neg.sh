#!/bin/bash
ID="$(git rev-parse --git-common-dir)"
[ "$ID" = "$KNOWN_ID" ] || exit 1
echo "same repository"
