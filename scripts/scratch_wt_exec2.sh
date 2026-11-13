#!/bin/bash
# Worktree cleanup executor (universal rules, "Worktree lifecycle", stage "Clean up").
#
# usage: scratch_wt_exec2.sh dry|apply WORKLIST
#
# WORKLIST: one item per line, `repo|path suffix as found in git worktree list|expected short HEAD|expected branch`.
# Blank lines and lines starting with # are ignored. Environment (all required except TT_VSCODE_STORAGE):
#   TT_WORKSPACE        directory that holds the repositories named in the worklist
#   TT_BACKUP_DIR       directory with <repo>/backup-branches.bundle for each repository (a gate compares branch tips)
#   TT_SCRATCH_PREFIX   a worktree path must start with this prefix, otherwise the item stops the run
#   TT_VSCODE_STORAGE   VS Code globalStorage/storage.json (default below; an override is meant for tests only);
#                       a worktree named there is in use. The file must exist.
#
# It removes a worktree and its branch only when every gate passes: the path comes from `git worktree list`
# (never built from a prefix), HEAD and branch are the expected ones, no process or open file uses the path, the
# tip is contained in origin/main, the branch tip equals the one in the backup bundle, and `git status --ignored`
# shows nothing but allowlisted generated paths. It uses `git worktree remove` and `git branch -d`, never a
# force option. An item that fails a gate is skipped and reported; nothing is guessed.
# The process gate fails closed: if `ps` or `lsof` cannot run, exits non-zero or prints nothing, no item can be shown
# to be unused, so every item is skipped with that reason. The process table and the open-file list are read once, at
# the start of the run, and the apply-time gate re-reads that snapshot.
# bash 3.2 safe; stops at the first non-zero exit of a state-changing command.
export GIT_OPTIONAL_LOCKS=0
MODE=${1:-dry}; [ "$MODE" = dry ] || [ "$MODE" = apply ] || { echo "usage: $0 dry|apply WORKLIST"; exit 2; }
WORKLIST=${2:-}; [ -n "$WORKLIST" ] && [ -f "$WORKLIST" ] || { echo "usage: $0 dry|apply WORKLIST (a readable worklist file is required)"; exit 2; }
for v in TT_WORKSPACE TT_BACKUP_DIR TT_SCRATCH_PREFIX; do
  eval "val=\${$v:-}"; [ -n "$val" ] || { echo "STOP: environment variable $v is required"; exit 2; }
done
M="$TT_WORKSPACE"
BK="$TT_BACKUP_DIR"
SCRATCH_PREFIX="$TT_SCRATCH_PREFIX"
ST="${TT_VSCODE_STORAGE:-$HOME/Library/Application Support/Code/User/globalStorage/storage.json}"
SKIPPED=""
die(){ echo "STOP: $*"; exit 1; }
run(){ echo "\$ $*"; "$@"; rc=$?; echo "[exit $rc]"; [ $rc -eq 0 ] || die "non-zero exit of a state-changing command"; }
skip(){ echo "   SKIP: $*"; SKIPPED="$SKIPPED
 - $*"; }
allow() {
  case "$1" in
    __pycache__/|*/__pycache__/|.pytest_cache/|*/.pytest_cache/) return 0;;
    scripts/doc_sync.py|scripts/doc_sync_tier2.py|schemas/execution-record.schema.json|.tooltempest.receipt.json) return 0;;
    docs/tooltempest/|docs/tooltempest/*|scripts/docops.py|scripts/docops_core/|scripts/docops_core/*|scripts/tooling.py|scripts/tooling_core/|scripts/tooling_core/*) return 0;;
    schemas/docops-*.schema.json|schemas/tooling-*.schema.json) return 0;;
    schemas/|scripts/check_manifest.py) return 0;;   # the two entries added by the Owner
  esac
  return 1
}
dig(){ printf '%s HEAD=%s status=%s lines=%s\n' "$1" "$(git -C "$M/$1" rev-parse HEAD)" "$(git -C "$M/$1" status --porcelain=v1 -z | shasum -a 256 | cut -c1-16)" "$(git -C "$M/$1" status --porcelain=v1 | wc -l | tr -d ' ')"; }

# repo|path suffix as found in `git worktree list`|expected short HEAD|expected branch
ITEMS=$(grep -v -e '^[[:space:]]*#' -e '^[[:space:]]*$' "$WORKLIST")
[ -n "$ITEMS" ] || { echo "STOP: the worklist has no items"; exit 2; }
# every item needs four non-empty fields and a head of at least 7 characters: an empty head would match any commit
while IFS='|' read -r f1 f2 f3 f4 f5; do
  [ -n "$f1" ] && [ -n "$f2" ] && [ -n "$f3" ] && [ -n "$f4" ] && [ -z "$f5" ] && [ "${#f3}" -ge 7 ] \
    || die "malformed worklist item (need repo|path suffix|head of 7+ characters|branch): $f1|$f2|$f3|$f4${f5:+|$f5}"
done <<WL_EOF
$ITEMS
WL_EOF
REPOS=$(printf '%s\n' "$ITEMS" | cut -d'|' -f1 | sort -u)
[ -f "$ST" ] || die "the VS Code storage file does not exist: $ST"

echo "mode=$MODE  snapshot $(date '+%F %T')"
BEFORE_ALL=""
for r in $REPOS; do d=$(dig $r); echo "BEFORE $d"; BEFORE_ALL="$BEFORE_ALL
$d"; done
echo "claude-like processes:"; ps -axo pid,command | grep -i -E '(^| )[^ ]*/claude( |$)' | grep -v grep | cut -c1-120

for r in $REPOS; do echo; echo "### git worktree list ($r), full paths:"; git -C "$M/$r" worktree list; done

# A tool that cannot run, exits non-zero or prints nothing (a machine always has processes and open files) leaves a
# reason in PS_ERR / LS_ERR; the gate then fails closed instead of reading an empty snapshot as "nothing uses it".
PS_ERR=""; LS_ERR=""
PSF=$(mktemp); LSR=$(mktemp); LSF=$(mktemp)
PS_MSG=$(ps -axo pid,ppid,command 2>&1 >"$PSF"); rc=$?
if [ $rc -ne 0 ]; then PS_ERR="ps unusable: exit $rc ${PS_MSG:0:120}"
elif [ ! -s "$PSF" ]; then PS_ERR="ps unusable: no output"; fi
LS_MSG=$(lsof -Fpcfn 2>&1 >"$LSR"); rc=$?
if [ $rc -ne 0 ]; then LS_ERR="lsof unusable: exit $rc ${LS_MSG:0:120}"
elif [ ! -s "$LSR" ]; then LS_ERR="lsof unusable: no output"
else
  awk '/^p/{p=substr($0,2)} /^c/{c=substr($0,2)} /^f/{f=substr($0,2)} /^n/{print p"\t"c"\t"f"\t"substr($0,2)}' < "$LSR" > "$LSF" || LS_ERR="lsof unusable: its output could not be parsed"
fi
[ -z "$PS_ERR" ] || echo "WARNING: $PS_ERR; every item will be skipped"
[ -z "$LS_ERR" ] || echo "WARNING: $LS_ERR; every item will be skipped"

# ---- resolve every path from the repository's own worktree list; stop if anything differs from the scope
echo; echo "### scope resolution (path from git worktree list, expected HEAD, expected branch)"
RESOLVED=""
while IFS='|' read -r R SUF TIP BR; do
  [ -z "$R" ] && continue
  P=$(git -C "$M/$R" worktree list --porcelain | awk -v suf="/$SUF" 'index($0,"worktree ")==1{p=substr($0,10); n=length(p); m=length(suf); if(n>=m && substr(p,n-m+1)==suf){print p}}')
  cnt=$(printf '%s\n' "$P" | grep -c .)
  [ "$cnt" = 1 ] || die "[$R] expected exactly one worktree ending with $SUF, found $cnt"
  case "$P" in "$SCRATCH_PREFIX"*) ;; *) die "[$R] resolved path outside the scratch area: $P";; esac
  H=$(git -C "$M/$R" worktree list --porcelain | awk -v p="$P" '$0=="worktree " p{f=1;next} f&&/^HEAD /{print $2; exit}')
  B=$(git -C "$M/$R" worktree list --porcelain | awk -v p="$P" '$0=="worktree " p{f=1;next} f&&/^branch /{print substr($0,8); exit} f&&/^$/{exit}')
  case "$H" in "$TIP"*) ;; *) die "[$R] $P: HEAD $H != expected $TIP";; esac
  [ "$B" = "refs/heads/$BR" ] || die "[$R] $P: branch '$B' != expected refs/heads/$BR"
  echo "[$R] $P"; echo "      expected HEAD $TIP (actual $H)  branch $BR"
  RESOLVED="$RESOLVED
$R|$P|$TIP|$BR"
done <<ITEMS_EOF
$ITEMS
ITEMS_EOF

gate() {  # $1 repo $2 path $3 tip $4 branch ; prints details; returns 0 if all gates pass
  R=$1; P=$2; TIP=$3; BR=$4; REPO="$M/$R"; G="git -C $REPO"; SID=$(echo "$P" | awk -F/ '{print $6}'); GATE_WHY=""
  [ -d "$P" ] || { echo "   gate: directory missing"; return 1; }
  n=$(grep -c -F -- "$P" "$ST"); echo "   gate storage.json matches: $n"; [ "$n" = 0 ] || return 1
  if [ -n "$PS_ERR$LS_ERR" ]; then GATE_WHY="${PS_ERR:+$PS_ERR }${LS_ERR}"; echo "   gate lsof/ps: cannot tell whether a process uses the worktree: $GATE_WHY"; return 1; fi
  LSM=$(grep -F -- "$P" "$LSF" | head -2); PSM=$(grep -F -e "$P" -e "$SID" "$PSF" | grep -v -F "$PSF" | head -2)
  echo "   gate lsof: ${LSM:-none}   ps: ${PSM:-none}"; [ -z "$LSM$PSM" ] || return 1
  H=$(git -C "$P" rev-parse HEAD); case "$H" in "$TIP"*) echo "   gate HEAD == $TIP: yes";; *) echo "   gate HEAD $H != $TIP"; return 1;; esac
  $G merge-base --is-ancestor "$H" origin/main && echo "   gate ancestor of origin/main: yes" || { echo "   gate ancestor of origin/main: NO"; return 1; }
  OUT=$(git -C "$P" status --porcelain=v1 --ignored); BAD=0; N=0
  while IFS= read -r l; do [ -z "$l" ] && continue; N=$((N+1)); c=${l:0:2}; f=${l:3}
    case "$c" in '!!') allow "$f" || { echo "      not allowlisted: $l"; BAD=1; };; *) echo "      blocking entry: $l"; BAD=1;; esac
  done <<EOF
$OUT
EOF
  echo "   gate status --ignored: $N entries, all '!!' allowlisted: $([ $BAD = 0 ] && echo yes || echo NO)"; [ $BAD = 0 ] || return 1
  cur=$($G rev-parse -q --verify "refs/heads/$BR") || { echo "   gate branch missing"; return 1; }
  ah=$($G rev-list --count origin/main.."$BR"); bt=$(git bundle list-heads "$BK/$R/backup-branches.bundle" "refs/heads/$BR" | cut -d' ' -f1)
  echo "   gate branch tip=${cur:0:12} ahead=$ah bundle=$([ "$bt" = "$cur" ] && echo equal || echo "DIFFERS(${bt:-none})")"
  case "$cur" in "$TIP"*) ;; *) echo "   gate branch tip != expected"; return 1;; esac
  [ "$ah" = 0 ] && [ "$bt" = "$cur" ] || return 1
  return 0
}

echo; echo "### gates per worktree"
PASSED=""
while IFS='|' read -r R P TIP BR; do
  [ -z "$R" ] && continue
  echo; echo "=== [$R] $P"
  if gate "$R" "$P" "$TIP" "$BR"; then echo "   RESULT: all gates pass"; PASSED="$PASSED
$R|$P|$TIP|$BR"; else skip "[$R] $P: gate failed${GATE_WHY:+ ($GATE_WHY)}"; fi
done <<EOF
$RESOLVED
EOF

echo; echo "### planned commands (for worktrees whose gates pass)"
while IFS='|' read -r R P TIP BR; do [ -z "$R" ] && continue
  echo "git -C $M/$R worktree remove $P"; echo "git -C $M/$R branch -d $BR   # after the remove, with tip re-check"
done <<EOF
$PASSED
EOF

if [ "$MODE" = dry ]; then printf "=== skipped (dry run):%s\n" "${SKIPPED:- none}"; echo "DRY RUN: nothing changed."; rm -f "$PSF" "$LSF" "$LSR"; exit 0; fi

echo; echo "### APPLY"
while IFS='|' read -r R P TIP BR; do
  [ -z "$R" ] && continue
  echo; echo "=== [$R] $P"
  gate "$R" "$P" "$TIP" "$BR" >/dev/null 2>&1 || { skip "[$R] $P: gate failed at apply time${GATE_WHY:+ ($GATE_WHY)}"; continue; }
  REPO="$M/$R"; G="git -C $REPO"
  cur=$($G rev-parse "refs/heads/$BR")
  echo "\$ git -C $REPO worktree remove $P"
  OUT=$($G worktree remove "$P" 2>&1); rc=$?; echo "[exit $rc] $OUT"
  if [ $rc -ne 0 ]; then skip "[$R] $P: worktree remove refused (exit $rc): $OUT"; continue; fi
  [ ! -e "$P" ] || die "path still exists after remove"
  cur2=$($G rev-parse -q --verify "refs/heads/$BR") || { skip "[$R] $BR: vanished"; continue; }
  [ "$cur2" = "$cur" ] || { skip "[$R] $BR: tip changed at re-check"; continue; }
  $G worktree list --porcelain | grep -qx -- "branch refs/heads/$BR" && { skip "[$R] $BR: still checked out"; continue; }
  echo "\$ git -C $REPO branch -d $BR"
  OUT=$($G branch -d "$BR" 2>&1); rc=$?; echo "[exit $rc] $OUT"
  [ $rc -eq 0 ] || skip "[$R] branch -d $BR refused (exit $rc): $OUT"
done <<EOF
$PASSED
EOF

echo; echo "################ FINAL REPORT"
for r in $REPOS; do
  G="git -C $M/$r"; echo; echo "##### $r"
  echo '$ git worktree list'; $G worktree list
  echo '$ git for-each-ref refs/heads'; $G for-each-ref refs/heads --format='%(objectname) %(refname:short)'
done
echo; for r in $REPOS; do echo "AFTER  $(dig $r)"; done
echo "BEFORE (for comparison):$BEFORE_ALL"
printf "=== skipped:%s\n" "${SKIPPED:- none}"
rm -f "$PSF" "$LSF" "$LSR"
