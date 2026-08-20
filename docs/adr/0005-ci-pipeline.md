# ADR-0005: GitHub Actions CI — DocOps Scenario Suite (Informational, Pre-Merge)

<!--
  REPOSITORY: mikkiola/tooltempest (docs/adr/0005-ci-pipeline.md).
  Number confirmed 2026-08-20: docs/adr/ held 0001-0004 at draft time.
  Re-verify at commit time per this repo's established practice (see
  ADR-0002's header comment).
-->

## Status

Proposed (design-approved via /spec interview, pre-implementation)

## Context

ToolTempest has one consumer today (`mikkiola/article-pipeline`). A
scenario suite exercising `doc_sync.py`'s pre-commit/pre-push behavior
— no-op, ordinary RECONCILE, genuine staged/unstaged conflict HARD
BLOCK, detached HEAD, no upstream, UNKNOWN-pattern touched/untouched,
`verify.py` missing/broken — has been run manually in past sessions,
but nothing in this repository runs it automatically. There is no
`.github/workflows/` and no `tests/` directory here today.

The Drift Warning mechanism's pre-push (not pre-commit) timing is a
prior, fixed decision (rationale: pre-commit must stay
instant/offline-safe; pre-push already requires network access for
the push itself) and is not reopened by this ADR. Drift Warning itself
also lives entirely in article-pipeline's own `scripts/hooks/pre-push`
(per README's "Connecting a new project" step 4), not in this
repository — this ADR does not touch it.

This repository's only precedent for verification is the opposite of
what CI requires: ADR-0004's Source section, and commit `b6a30ae`'s
message, both describe verifying `doc_sync_tier2.py` changes "via a
scratch test (not committed)." CI cannot run a test that was
deliberately thrown away — it requires committed, repeatable test
code. This ADR treats that as a deliberate, explicit break from the
scratch-test convention, not an oversight.

## Decision

- **Trigger:** `pull_request` targeting `main`. Not `push` — visibility
  before merge is the goal even though this check does not yet block
  merge (see Enforcement).
- **Enforcement:** Informational only. The workflow runs and reports
  pass/fail on the PR; it is *not* wired into branch protection as a
  required status check in this ADR. Turning it into a required check
  is a follow-up decision, made once the suite has run for a while and
  proven itself.
- **Scope:** The seven named DocOps scenarios only. General code
  hygiene (lint, type-checking) is explicitly out of scope for this
  ADR — considered and reverted mid-interview; it would be a separate,
  later ADR if wanted.
- **Test harness:** A synthetic fixture directory committed to this
  repository (e.g. `tests/fixtures/`) — a minimal fake `SPEC.md`,
  `CHECKPOINT.md`, and stub `verify.py` standing in for a consuming
  project, fully self-contained with no external repo dependency. This
  is a deliberate override of the scratch-test convention described in
  Context above: from this ADR forward, verification code for
  `doc_sync.py`/`doc_sync_tier2.py` behavior may be committed when it
  serves automated CI, superseding the scratch-only pattern for this
  purpose. Prior scratch-test usage elsewhere in this repo's history is
  not retroactively changed by this ADR.
- **Scenario content source and method:** the seven scenarios are
  re-derived against `doc_sync.py`'s own pre-commit/pre-push entry
  points using the *method* demonstrated by
  `mikkiola/article-pipeline`'s `.github/scripts/test-reconcile.sh`
  and `.github/scripts/test_reconcile_error_path.py` (confirmed
  working as of that repo's commit `0fb1676`) — not a literal port of
  either file's test bodies, which exercise a different mechanism
  (`reconcile.py` / `apply_tier2_sync()`, ADR-0033/0034/0035) that
  happens to share ToolTempest ancestry, not the `doc_sync.py`
  pre-commit/pre-push contract this ADR's suite targets. The method:
  - an isolated scratch repo per scenario (`mktemp -d`, `trap
    'rm -rf' EXIT`), containing a bare local `origin.git` plus one or
    more work clones — no contact with a real remote or the GitHub API
  - the vendored files under test (`doc_sync.py`, and the fixture's
    fake `SPEC.md`/`CHECKPOINT.md`/`verify.py`) copied into each work
    clone before it is seeded and committed
  - each scenario shapes the work clone's git state to match what it
    tests (e.g. `git checkout --detach` for the detached-HEAD
    scenario; a work clone with its upstream unset for no-upstream;
    two clones of the same origin racing a push, from the
    article-pipeline suite's concurrent-push case, adapted if a
    parallel scenario is found useful here) and then invokes
    `doc_sync.py pre-commit` / `doc_sync.py pre-push` as a subprocess,
    asserting on exit code and stdout/the resulting git state
  - the one scenario a pure git-scratch setup structurally cannot
    reach — a genuine exception mid-flow, if `doc_sync.py`'s own logic
    has an equivalent unreachable-by-git-state branch — follows
    `test_reconcile_error_path.py`'s pattern instead:
    `importlib.util.spec_from_file_location` to load the module fresh,
    `unittest.mock.patch.object` to force the failure, asserting the
    error path logs cleanly rather than crashing uncaught. Whether
    `doc_sync.py` actually has such a branch (unlike `reconcile.py`,
    which always diffs a file against its own on-disk content) needs
    confirming during implementation, not assumed from the
    article-pipeline precedent.
- **Python version:** Pinned to an exact version via `actions/setup-python`
  — `3.14`, matching the version (`3.14.5`) the scripts are actually
  developed and run against locally, not a floating "latest."

## Options Considered

- **Trigger — push to main / PR to main / both:** chose PR-only. Pure
  post-hoc `push`-triggered visibility (after merge) was rejected as
  weaker than pre-merge visibility, even without blocking power yet.
- **Enforcement — required check / informational:** chose
  informational. Matches the suite's status as newly-automated and
  unproven; required-check is an explicit future step, not bundled in
  here.
- **Test harness — committed fixtures / external checkout of
  article-pipeline / no persistent harness (regenerate per run):**
  chose committed fixtures. An external checkout would couple
  ToolTempest's CI to a specific external repo and commit, cutting
  against client/consumer independence (README, "Client independence").
  Regenerating fixtures inline was considered as a lighter-touch way to
  honor the scratch-test convention's spirit, but committed, versioned
  fixtures were chosen instead so the scenario suite itself is
  reviewable and diffable like any other test asset.
- **Scope — scenario suite only / plus lint+type-check:** chose
  scenario-suite-only. Broader hygiene tooling was considered (this
  being the first CI this repo will ever have, a natural point to add
  it) but reverted to keep this ADR tightly scoped to the actual gap
  it closes.

## Why

The scenario suite exists to catch exactly the class of bug the
background for this work names: a HARD BLOCK, a silent RECONCILE
failure, or a broken `verify.py` handoff going unnoticed until a human
happens to hit it manually. Automating it at PR time, even
non-blocking, converts "someone eventually notices" into "visible on
every PR." Making it a required check immediately, before the suite
has run for real for a while, risks blocking merges on a suite that
hasn't yet proven it doesn't false-positive — informational-first is
the lower-risk sequencing.

## Scope / Invariants

- Does not touch Drift Warning or its pre-push timing — both are
  fixed, prior decisions, out of scope here.
- Does not touch `scripts/verify.py` (a project-local script in each
  consumer, not part of ToolTempest) beyond what the stub in the test
  fixture needs to simulate its presence/absence/brokenness.
- Does not make this check a required branch-protection status check.
  That is a distinct, future decision.

## Open Questions / Dependencies

- **Resolved** (architect chat, 2026-08-20): source method identified
  as `mikkiola/article-pipeline`'s `.github/scripts/test-reconcile.sh`
  and `.github/scripts/test_reconcile_error_path.py`, confirmed
  working at that repo's commit `0fb1676`. See "Scenario content
  source and method" above for what transfers (the isolated
  bare-origin scratch-repo technique) and what does not (the literal
  test bodies, which target a different mechanism).
- **Still open, for implementation, not this ADR:** whether
  `doc_sync.py`'s own control flow has a branch structurally
  unreachable by git-state scenarios alone (mirroring why
  `reconcile.py` needed a separate monkeypatch-based test) — determine
  this by reading `doc_sync.py` during implementation before assuming
  the mock-based case is needed.

## Consequences

- This repository gains its first committed test code and its first
  GitHub Actions workflow.
- The "scratch test, not committed" convention (ADR-0004) is
  explicitly superseded for DocOps scenario-suite verification going
  forward — future ADRs or implementation work should cite this ADR,
  not ADR-0004's Source section, as the current convention for this
  category of test.
- A later ADR is expected once the suite has run for a while, to
  decide whether to promote it to a required status check.

## Source

`/spec` interview session, 2026-08-20, scoped to `mikkiola/tooltempest`
only (architect context provided as background, not as answers — the
interview was run against the session user directly). Live repo
findings during the interview: no `.github/workflows/`, no `tests/`,
confirmed via direct inspection; scratch-test convention confirmed via
ADR-0004 and commit `b6a30ae`. Scenario-source method resolved
post-interview via architect chat, citing `mikkiola/article-pipeline`
`.github/scripts/test-reconcile.sh` and
`.github/scripts/test_reconcile_error_path.py` at that repo's commit
`0fb1676` — read directly from a local checkout to confirm the method
before writing it into this ADR, not taken on the architect's
description alone.
