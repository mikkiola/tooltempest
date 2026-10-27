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
#                                   "nothing happens" case.
#   well-formed, ordinary commit -> Case 2 (clean pass-through: nothing
#                                   MALFORMED, DETECT's own gate exits
#                                   0 before VALIDATE ever runs)
#   detached HEAD                -> Case 4
#   no upstream                  -> Case 5
#   UNKNOWN touched/untouched    -> Case 6a/6b
#   verify.py missing/broken     -> Case 7a/7b
#   malformed doc-owned SPEC     -> Case 8 (added in Stage 4: the one
#                                   structural-failure path consumers hit
#                                   that no case exercised)
#   untouched MALFORMED file     -> Case 10 (added in Stage 4: blocks an
#                                   unrelated commit, unlike UNKNOWN)
#   RECORD and STAGE             -> Case 9 (added in Stage 4: the only path
#                                   that writes a run record; Case 2 and
#                                   Case 1 pin that ordinary runs write none)
#
# What this suite covers, and what it does not (Stage 4): it tests the
# LEGACY Tier 1 entry points, scripts/doc_sync.py pre-commit/pre-push, driven
# by tests/fixtures/verify.py, a stub that implements only the output contract
# doc_sync.py reads from a consumer's own scripts/verify.py (see that file's
# header). The stub is not the production SPEC parser. The production parser
# and the docops result contract are covered by tests/docops; a pass there
# is no evidence for Tier 1, and a pass here is none for docops.py.
#
# Case numbering intentionally keeps its gaps (no 2b/2c/3) rather than
# renumbering what remains -- ADR-0010 (docs/adr/0010-retire-checkpoint-md-support.md)
# retired CHECKPOINT.md support from doc_sync.py entirely (RECONCILE,
# find_checkpoint_missing_fields(), staged_blob_text(), and the
# "checkpoint" pattern's every branch are gone from scripts/doc_sync.py
# now, not just deprecated), and the cases that lived at 2b/2c/3
# (missing required field(s), genuine staged/unstaged conflict) tested
# exactly that removed code path. Kept for the historical record, not
# renumbered forward, so old commit messages/PRs referencing a specific
# case number by that number still mean the same test today.
#
# Resolved during implementation, per ADR-0005's Open Questions
# ("Still open ... determine this by reading doc_sync.py during
# implementation before assuming the mock-based case is needed"):
# unlike reconcile.py (whose apply_tier2_sync() diff is always empty
# by construction, making its write_text() call unreachable by git
# state alone -- see article-pipeline's test_reconcile_error_path.py),
# every named scenario here is reachable by shaping git state and
# fixture content alone; no monkeypatch-based test is needed here, and
# this suite has no Python counterpart to test_reconcile_error_path.py.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOC_SYNC_PY="${REPO_ROOT}/scripts/doc_sync.py"
FIXTURES_DIR="${REPO_ROOT}/tests/fixtures"
FIXTURE_SPEC="${FIXTURES_DIR}/SPEC.md"
FIXTURE_SPEC_UNKNOWN="${FIXTURES_DIR}/SPEC-no-milestones.md"
FIXTURE_SPEC_MALFORMED="${FIXTURES_DIR}/SPEC-malformed.md"
FIXTURE_VERIFY="${FIXTURES_DIR}/verify.py"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

[ -f "$DOC_SYNC_PY" ] || fail "${DOC_SYNC_PY} not found"
[ -f "$FIXTURE_SPEC" ] || fail "${FIXTURE_SPEC} not found"
[ -f "$FIXTURE_SPEC_UNKNOWN" ] || fail "${FIXTURE_SPEC_UNKNOWN} not found"
[ -f "$FIXTURE_SPEC_MALFORMED" ] || fail "${FIXTURE_SPEC_MALFORMED} not found"
[ -f "$FIXTURE_VERIFY" ] || fail "${FIXTURE_VERIFY} not found"

SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT

# Seeds a bare "origin" plus one work clone with the vendored
# scripts/doc_sync.py + fixture scripts/verify.py in place (normally
# installed by a consuming project's own sync-tooling; copied directly
# here since that script talks to a real remote). $2 controls whether
# the root-level fixture SPEC.md is seeded too:
#   with-spec -> the well-formed fixture SPEC.md (Cases 2, 4-8's baseline)
#   no-spec   -> none present (Case 1's no-op baseline)
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
    if [ "$mode" = "with-spec" ]; then
      cp "$FIXTURE_SPEC" SPEC.md
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

# --- Case 2: well-formed fixture, ordinary unrelated edit -- nothing
# MALFORMED, so the hook must pass clean: exit 0, nothing touched.
WORK2="$(seed_repo case2 with-spec)"
(cd "$WORK2" && printf '\nUnrelated edit.\n' >> SPEC.md && git add SPEC.md)
SPEC2_BEFORE="$(cat "${WORK2}/SPEC.md")"
EXIT2=0
OUT2="$(run_pre_commit "$WORK2" 2>&1)" || EXIT2=$?
[ "$EXIT2" -eq 0 ] || fail "case 2: exit code was $EXIT2, expected 0, output: $OUT2"
[ "$(cat "${WORK2}/SPEC.md")" = "$SPEC2_BEFORE" ] || fail "case 2: the hook rewrote SPEC.md; Tier 1 must never modify a doc-owned file"
[ "$(record_count "$WORK2")" -eq 0 ] || fail "case 2: an ordinary all-OK commit must leave no run record (it never reaches RECORD)"
[ -z "$(cd "$WORK2" && git diff --cached --name-only | grep '^\.tempest/')" ] || fail "case 2: an ordinary all-OK commit must stage nothing under .tempest/"

# --- Case 4: detached HEAD -- must behave identically to a normal
# branch checkout for both entry points; doc_sync.py's repo_root()
# (git rev-parse --show-toplevel) and its staged/unstaged diffs don't
# care about HEAD's ref-vs-detached state, so nothing here should fail.
WORK4="$(seed_repo case4 with-spec)"
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
# component (component-unknown/SPEC.md, a SPEC.md with no "## Milestones"
# section) is UNKNOWN by construction. Touching it must block the commit; leaving
# it alone while committing something unrelated must not.
seed_unknown_component() {
  local name="$1"
  local work
  work="$(seed_repo "$name" with-spec)"
  (
    cd "$work"
    mkdir -p component-unknown
    cp "$FIXTURE_SPEC_UNKNOWN" component-unknown/SPEC.md
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
WORK7A="$(seed_repo case7a with-spec)"
(cd "$WORK7A" && rm scripts/verify.py && printf '\nedit.\n' >> SPEC.md && git add SPEC.md)
EXIT7AC=0
OUT7AC="$(run_pre_commit "$WORK7A" 2>&1)" || EXIT7AC=$?
[ "$EXIT7AC" -eq 1 ] || fail "case 7a: pre-commit exit code was $EXIT7AC, expected 1, output: $OUT7AC"
echo "$OUT7AC" | grep -q 'did not produce readable output' || fail "case 7a: expected missing-verify.py message, got: $OUT7AC"
EXIT7AP=0
OUT7AP="$(run_pre_push "$WORK7A" 2>&1)" || EXIT7AP=$?
[ "$EXIT7AP" -eq 1 ] || fail "case 7a: pre-push exit code was $EXIT7AP, expected 1, output: $OUT7AP"
echo "$OUT7AP" | grep -q 'did not produce readable output' || fail "case 7a: expected missing-verify.py message on pre-push, got: $OUT7AP"

WORK7B="$(seed_repo case7b with-spec)"
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

# --- Case 8: a malformed doc-owned SPEC.md (a milestone checkbox with no
# description). The stub reports MALFORMED and exits 1; doc_sync.py must
# block the commit with the "still failed" message (never record, never
# pass) and block the push, without rewriting the file or a traceback.
WORK8="$(seed_repo case8 with-spec)"
(cd "$WORK8" && cp "$FIXTURE_SPEC_MALFORMED" SPEC.md && git add SPEC.md)
SPEC8_BEFORE="$(cat "${WORK8}/SPEC.md")"
EXIT8C=0
OUT8C="$(run_pre_commit "$WORK8" 2>&1)" || EXIT8C=$?
[ "$EXIT8C" -eq 1 ] || fail "case 8: pre-commit exit code was $EXIT8C, expected 1, output: $OUT8C"
echo "$OUT8C" | grep -q 'scripts/verify.py still failed' || fail "case 8: expected the still-failed block message, got: $OUT8C"
echo "$OUT8C" | grep -qi 'Traceback' && fail "case 8: got an uncaught traceback instead of a clean failure"
[ "$(record_count "$WORK8")" -eq 0 ] || fail "case 8: a blocked commit must not leave a run record"
[ "$(cat "${WORK8}/SPEC.md")" = "$SPEC8_BEFORE" ] || fail "case 8: the hook rewrote the malformed SPEC.md"
EXIT8P=0
OUT8P="$(run_pre_push "$WORK8" 2>&1)" || EXIT8P=$?
[ "$EXIT8P" -eq 1 ] || fail "case 8: pre-push exit code was $EXIT8P, expected 1, output: $OUT8P"
echo "$OUT8P" | grep -q 'scripts/verify.py did not pass' || fail "case 8: expected the pre-push block message, got: $OUT8P"

# --- Case 9: the one path that reaches RECORD and STAGE. A doc-owned file is
# MALFORMED when DETECT scans it and the VALIDATE re-run then passes (the tree
# changed between the two scans, or the verifier gave a different answer for the
# same tree; the stub below models the second). The stub reports MALFORMED on
# its first call and OK on its second. doc_sync.py must run both steps, write exactly one
# run record and stage that record, and nothing else.
WORK9="$(seed_repo case9 with-spec)"
cat > "${WORK9}/scripts/verify.py" <<'PY'
#!/usr/bin/env python3
import json, sys
from pathlib import Path
root = Path(__file__).resolve().parent.parent
counter = root.parent / (root.name + ".verify-calls")
calls = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(calls + 1))
spec = str(root / "SPEC.md")
status = "MALFORMED" if calls == 0 else "OK"
print(json.dumps([{"spec_path": spec, "pattern": "inline_spec", "source_file": spec,
                   "structure": {"status": status}}]))
sys.exit(1 if calls == 0 else 0)
PY
(cd "$WORK9" && printf '\nedit.\n' >> SPEC.md && git add SPEC.md)
EXIT9=0
OUT9="$(run_pre_commit "$WORK9" 2>&1)" || EXIT9=$?
[ "$EXIT9" -eq 0 ] || fail "case 9: exit code was $EXIT9, expected 0, output: $OUT9"
echo "$OUT9" | grep -q 'OK: staged .tempest/runs/docops_' || fail "case 9: expected the staged-record message, got: $OUT9"
[ "$(cat "${SCRATCH}/case9-work.verify-calls")" = "2" ] || fail "case 9: verify.py should have run twice (DETECT, VALIDATE)"
[ "$(record_count "$WORK9")" -eq 1 ] || fail "case 9: exactly one run record should have been written"
STAGED9="$(cd "$WORK9" && git diff --cached --name-only | sort)"
[ "$(echo "$STAGED9" | wc -l | tr -d ' ')" -eq 2 ] || fail "case 9: expected SPEC.md and the record staged, got: $STAGED9"
echo "$STAGED9" | grep -q '^SPEC.md$' || fail "case 9: SPEC.md should still be staged, got: $STAGED9"
echo "$STAGED9" | grep -q '^\.tempest/runs/docops_.*\.json$' || fail "case 9: the run record should be staged, got: $STAGED9"
RECORD9="$(ls "${WORK9}"/.tempest/runs/docops_*.json)"
python3 - "$RECORD9" <<'PY' || fail "case 9: the run record does not have the documented shape"
import json, sys
record = json.load(open(sys.argv[1]))
assert record["steps"] == ["detect", "validate", "record"], record["steps"]
assert record["counters"] == {"scanned": 1, "affected": 1, "updated": 0}, record["counters"]
assert record["result"] == "SUCCESS" and record["hook"] == "pre-commit" and "commit_sha" not in record
PY

# --- Case 10: a MALFORMED doc-owned file blocks a commit that does not touch
# it. Unlike the UNKNOWN pattern (Case 6b), the MALFORMED decision is not limited
# to staged components: an unrelated commit is blocked while the file is broken.
WORK10="$(seed_repo case10 with-spec)"
(cd "$WORK10" && cp "$FIXTURE_SPEC_MALFORMED" SPEC.md && git add SPEC.md && git commit -q -m "malformed spec already committed" \
  && printf 'unrelated\n' > NOTES.md && git add NOTES.md)
EXIT10=0
OUT10="$(run_pre_commit "$WORK10" 2>&1)" || EXIT10=$?
[ "$EXIT10" -eq 1 ] || fail "case 10: exit code was $EXIT10, expected 1, output: $OUT10"
echo "$OUT10" | grep -q 'scripts/verify.py still failed' || fail "case 10: expected the still-failed block message, got: $OUT10"
[ "$(record_count "$WORK10")" -eq 0 ] || fail "case 10: a blocked commit must not leave a run record"
[ "$(cd "$WORK10" && git diff --cached --name-only)" = "NOTES.md" ] || fail "case 10: nothing but the unrelated file may be staged"

echo "OK: all doc_sync.py scenarios passed (cases 1-10)."
