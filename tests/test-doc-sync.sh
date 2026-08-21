#!/usr/bin/env bash
# Scenario suite for scripts/doc_sync.py's pre-commit/pre-push entry
# points. Method ported from mikkiola/article-pipeline's
# .github/scripts/test-reconcile.sh (confirmed working at that repo's
# commit 0fb1676) -- an isolated scratch repo per scenario (mktemp -d,
# trap 'rm -rf' EXIT), a bare local origin.git plus one or more work
# clones, no contact with a real remote or the GitHub API. Only the
# METHOD is ported: doc_sync.py's own pre-commit/pre-push contract
# differs from reconcile.py's, so the scenario bodies below are
# specific to it, not copied from that file. See ADR-0005
# (docs/adr/0005-ci-pipeline.md) for the full contract this suite
# verifies.
#
# Scenario mapping to ADR-0005's Context list ("no-op, ordinary
# RECONCILE, genuine staged/unstaged conflict HARD BLOCK, detached
# HEAD, no upstream, UNKNOWN-pattern touched/untouched, verify.py
# missing/broken"):
#
#   no-op                        -> Case 1. Interpreted as "no SPEC.md
#                                   files found at all" (doc_sync.py's
#                                   verify_results is None + exit_code
#                                   == 2 branch) -- the most literal
#                                   "nothing happens" case, and
#                                   structurally distinct from Case 2
#                                   ("well-formed, nothing to
#                                   reconcile" would otherwise overlap
#                                   with it).
#   ordinary RECONCILE           -> Case 2
#   staged/unstaged conflict     -> Case 3
#   detached HEAD                -> Case 4
#   no upstream                  -> Case 5
#   UNKNOWN touched/untouched    -> Case 6a/6b
#   verify.py missing/broken     -> Case 7a/7b
#
# Resolved during implementation, per ADR-0005's Open Questions
# ("Still open ... determine this by reading doc_sync.py during
# implementation before assuming the mock-based case is needed"):
# unlike reconcile.py (whose apply_tier2_sync() diff is always empty
# by construction, making its write_text() call unreachable by git
# state alone -- see article-pipeline's test_reconcile_error_path.py),
# doc_sync.py's own reconcile() writes a genuinely different,
# newly-computed CHECKPOINT.md content derived from real structural
# findings. Every one of the seven named scenarios is reachable by
# shaping git state and fixture content alone; no monkeypatch-based
# test is needed here, and this suite has no Python counterpart to
# test_reconcile_error_path.py.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOC_SYNC_PY="${REPO_ROOT}/scripts/doc_sync.py"
FIXTURES_DIR="${REPO_ROOT}/tests/fixtures"
FIXTURE_SPEC="${FIXTURES_DIR}/SPEC.md"
FIXTURE_CHECKPOINT="${FIXTURES_DIR}/CHECKPOINT.md"
FIXTURE_VERIFY="${FIXTURES_DIR}/verify.py"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

[ -f "$DOC_SYNC_PY" ] || fail "${DOC_SYNC_PY} not found"
[ -f "$FIXTURE_SPEC" ] || fail "${FIXTURE_SPEC} not found"
[ -f "$FIXTURE_CHECKPOINT" ] || fail "${FIXTURE_CHECKPOINT} not found"
[ -f "$FIXTURE_VERIFY" ] || fail "${FIXTURE_VERIFY} not found"

SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT

# Seeds a bare "origin" plus one work clone with the vendored
# scripts/doc_sync.py + fixture scripts/verify.py in place (normally
# installed by a consuming project's own sync-tooling; copied directly
# here since that script talks to a real remote). $2 controls whether
# the root-level fixture SPEC.md/CHECKPOINT.md pair is seeded too:
#   with-checkpoint -> both present, well-formed (Cases 2-6's baseline)
#   no-spec         -> neither present (Case 1's no-op baseline)
seed_repo() {
  local name="$1" mode="$2"
  local origin="${SCRATCH}/${name}-origin.git"
  local work="${SCRATCH}/${name}-work"
  git init --bare -q "$origin"
  git -C "$origin" symbolic-ref HEAD refs/heads/main
  git clone -q "$origin" "$work"
  (
    cd "$work"
    git config user.email test@test.local
    git config user.name test
    mkdir -p scripts
    cp "$DOC_SYNC_PY" scripts/doc_sync.py
    cp "$FIXTURE_VERIFY" scripts/verify.py
    if [ "$mode" = "with-checkpoint" ]; then
      cp "$FIXTURE_SPEC" SPEC.md
      cp "$FIXTURE_CHECKPOINT" CHECKPOINT.md
    fi
    git add -A
    git commit -q -m seed
    git push -q origin HEAD:main
  )
  echo "$work"
}

# Case 5's variant: a standalone repo with no origin/remote configured
# at all (not cloned), to test doc_sync.py under "no upstream".
seed_standalone_repo() {
  local name="$1"
  local work="${SCRATCH}/${name}-work"
  mkdir -p "$work"
  (
    cd "$work"
    git init -q
    git config user.email test@test.local
    git config user.name test
    mkdir -p scripts
    cp "$DOC_SYNC_PY" scripts/doc_sync.py
    cp "$FIXTURE_VERIFY" scripts/verify.py
    cp "$FIXTURE_SPEC" SPEC.md
    cp "$FIXTURE_CHECKPOINT" CHECKPOINT.md
    git add -A
    git commit -q -m seed
  )
  echo "$work"
}

run_pre_commit() {
  ( cd "$1" && python3 scripts/doc_sync.py pre-commit )
}

run_pre_push() {
  ( cd "$1" && python3 scripts/doc_sync.py pre-push )
}

record_count() {
  find "$1/.tempest/runs" -maxdepth 1 -name 'docops_*.json' 2>/dev/null | wc -l | tr -d ' '
}

# --- Case 1: no-op -- no SPEC.md files found at all. doc_sync.py must
# recognize scripts/verify.py's exit code 2 as a legitimate no-op, not
# a crash, and exit 0 without touching anything.
WORK1="$(seed_repo case1 no-spec)"
(cd "$WORK1" && printf 'unrelated\n' > NOTES.md && git add NOTES.md)
EXIT1=0
OUT1="$(run_pre_commit "$WORK1" 2>&1)" || EXIT1=$?
[ "$EXIT1" -eq 0 ] || fail "case 1: exit code was $EXIT1, expected 0, output: $OUT1"
echo "$OUT1" | grep -q 'no SPEC.md files found' || fail "case 1: expected no-op message, got: $OUT1"
[ "$(record_count "$WORK1")" -eq 0 ] || fail "case 1: no run record should have been written"

# --- Case 2: ordinary RECONCILE -- a staged CHECKPOINT.md missing a
# required field, staged the ordinary way (git add && commit, index ==
# working tree). RECONCILE must fill the field with TODO, VALIDATE
# must then pass, and a run record must be written and staged.
WORK2="$(seed_repo case2 with-checkpoint)"
(
  cd "$WORK2"
  sed -i.bak '/^- done-when:/d' CHECKPOINT.md && rm -f CHECKPOINT.md.bak
  git add CHECKPOINT.md
)
EXIT2=0
OUT2="$(run_pre_commit "$WORK2" 2>&1)" || EXIT2=$?
[ "$EXIT2" -eq 0 ] || fail "case 2: exit code was $EXIT2, expected 0, output: $OUT2"
grep -q '^- done-when: TODO' "${WORK2}/CHECKPOINT.md" || fail "case 2: done-when field was not reconciled"
git -C "$WORK2" diff --cached --name-only | grep -q '^CHECKPOINT\.md$' || fail "case 2: CHECKPOINT.md was not (re-)staged"
[ "$(record_count "$WORK2")" -eq 1 ] || fail "case 2: expected exactly one run record written"
git -C "$WORK2" diff --cached --name-only | grep -q '\.tempest/runs/docops_.*\.json$' || fail "case 2: run record was not staged"

# --- Case 3: genuine staged/unstaged conflict HARD BLOCK -- the staged
# blob is itself malformed (missing "status"), AND a further unstaged
# edit exists on top of it (index and working tree diverge). RECONCILE
# must refuse to touch anything and block the commit.
WORK3="$(seed_repo case3 with-checkpoint)"
(
  cd "$WORK3"
  sed -i.bak '/^- status:/d' CHECKPOINT.md && rm -f CHECKPOINT.md.bak
  git add CHECKPOINT.md
  printf '\n<!-- further unstaged edit -->\n' >> CHECKPOINT.md
)
EXIT3=0
OUT3="$(run_pre_commit "$WORK3" 2>&1)" || EXIT3=$?
[ "$EXIT3" -eq 1 ] || fail "case 3: exit code was $EXIT3, expected 1, output: $OUT3"
echo "$OUT3" | grep -q 'already structurally malformed' || fail "case 3: expected HARD BLOCK message, got: $OUT3"
echo "$OUT3" | grep -q 'CHECKPOINT.md' || fail "case 3: expected CHECKPOINT.md named in the block message"
[ -d "${WORK3}/.tempest" ] && fail "case 3: nothing should have been touched, but .tempest/ was created"

# --- Case 4: detached HEAD -- must behave identically to a normal
# branch checkout for both entry points; doc_sync.py's repo_root()
# (git rev-parse --show-toplevel) and its staged/unstaged diffs don't
# care about HEAD's ref-vs-detached state, so nothing here should fail.
WORK4="$(seed_repo case4 with-checkpoint)"
(cd "$WORK4" && git checkout -q --detach)
(cd "$WORK4" && printf '\nDetached-HEAD edit.\n' >> SPEC.md && git add SPEC.md)
EXIT4C=0
OUT4C="$(run_pre_commit "$WORK4" 2>&1)" || EXIT4C=$?
[ "$EXIT4C" -eq 0 ] || fail "case 4: pre-commit exit code was $EXIT4C, expected 0, output: $OUT4C"
EXIT4P=0
OUT4P="$(run_pre_push "$WORK4" 2>&1)" || EXIT4P=$?
[ "$EXIT4P" -eq 0 ] || fail "case 4: pre-push exit code was $EXIT4P, expected 0, output: $OUT4P"

# --- Case 5: no upstream -- a standalone repo with no remote
# configured at all. doc_sync.py never fetches or pushes, so neither
# entry point should care.
WORK5="$(seed_standalone_repo case5)"
(cd "$WORK5" && printf '\nNo-upstream edit.\n' >> SPEC.md && git add SPEC.md)
EXIT5C=0
OUT5C="$(run_pre_commit "$WORK5" 2>&1)" || EXIT5C=$?
[ "$EXIT5C" -eq 0 ] || fail "case 5: pre-commit exit code was $EXIT5C, expected 0, output: $OUT5C"
EXIT5P=0
OUT5P="$(run_pre_push "$WORK5" 2>&1)" || EXIT5P=$?
[ "$EXIT5P" -eq 0 ] || fail "case 5: pre-push exit code was $EXIT5P, expected 0, output: $OUT5P"

# --- Case 6a/6b: UNKNOWN-pattern touched/untouched. A second
# component (component-unknown/SPEC.md, no paired CHECKPOINT.md) is
# UNKNOWN by construction. Touching it must block the commit; leaving
# it alone while committing something unrelated must not.
seed_unknown_component() {
  local name="$1"
  local work
  work="$(seed_repo "$name" with-checkpoint)"
  (
    cd "$work"
    mkdir -p component-unknown
    cp "$FIXTURE_SPEC" component-unknown/SPEC.md
    git add component-unknown/SPEC.md
    git commit -q -m "add unknown component"
  )
  echo "$work"
}

WORK6A="$(seed_unknown_component case6a)"
(cd "$WORK6A" && printf '\nTouched.\n' >> component-unknown/SPEC.md && git add component-unknown/SPEC.md)
EXIT6A=0
OUT6A="$(run_pre_commit "$WORK6A" 2>&1)" || EXIT6A=$?
[ "$EXIT6A" -eq 1 ] || fail "case 6a: exit code was $EXIT6A, expected 1, output: $OUT6A"
echo "$OUT6A" | grep -q 'unrecognized doc structure' || fail "case 6a: expected UNKNOWN-pattern block message, got: $OUT6A"
echo "$OUT6A" | grep -q 'component-unknown/SPEC.md' || fail "case 6a: expected component-unknown/SPEC.md named, got: $OUT6A"

WORK6B="$(seed_unknown_component case6b)"
(cd "$WORK6B" && printf '\nUnrelated edit.\n' >> SPEC.md && git add SPEC.md)
EXIT6B=0
OUT6B="$(run_pre_commit "$WORK6B" 2>&1)" || EXIT6B=$?
[ "$EXIT6B" -eq 0 ] || fail "case 6b: exit code was $EXIT6B, expected 0 (untouched UNKNOWN component must not block), output: $OUT6B"

# --- Case 7a/7b: verify.py missing/broken. Both must fail cleanly
# (exit 1, "did not produce readable output"), never crash uncaught,
# for both entry points.
WORK7A="$(seed_repo case7a with-checkpoint)"
(cd "$WORK7A" && rm scripts/verify.py && printf '\nedit.\n' >> SPEC.md && git add SPEC.md)
EXIT7AC=0
OUT7AC="$(run_pre_commit "$WORK7A" 2>&1)" || EXIT7AC=$?
[ "$EXIT7AC" -eq 1 ] || fail "case 7a: pre-commit exit code was $EXIT7AC, expected 1, output: $OUT7AC"
echo "$OUT7AC" | grep -q 'did not produce readable output' || fail "case 7a: expected missing-verify.py message, got: $OUT7AC"
EXIT7AP=0
OUT7AP="$(run_pre_push "$WORK7A" 2>&1)" || EXIT7AP=$?
[ "$EXIT7AP" -eq 1 ] || fail "case 7a: pre-push exit code was $EXIT7AP, expected 1, output: $OUT7AP"
echo "$OUT7AP" | grep -q 'did not produce readable output' || fail "case 7a: expected missing-verify.py message on pre-push, got: $OUT7AP"

WORK7B="$(seed_repo case7b with-checkpoint)"
(
  cd "$WORK7B"
  printf '#!/usr/bin/env python3\nimport sys\nsys.exit(1)\n' > scripts/verify.py
  printf '\nedit.\n' >> SPEC.md
  git add SPEC.md
)
EXIT7BC=0
OUT7BC="$(run_pre_commit "$WORK7B" 2>&1)" || EXIT7BC=$?
[ "$EXIT7BC" -eq 1 ] || fail "case 7b: pre-commit exit code was $EXIT7BC, expected 1, output: $OUT7BC"
echo "$OUT7BC" | grep -q 'did not produce readable output' || fail "case 7b: expected broken-verify.py message, got: $OUT7BC"
echo "$OUT7BC" | grep -qi 'Traceback' && fail "case 7b: got an uncaught traceback instead of a clean failure"

echo "OK: all doc_sync.py scenarios passed (cases 1-7)."
