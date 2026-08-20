# ADR-0006: Vendor Manifest — MANIFEST.txt as the Consumer File List (Option A)

<!--
  REPOSITORY: mikkiola/tooltempest (docs/adr/0006-vendor-manifest.md).
  Number confirmed 2026-08-20: docs/adr/ held 0001-0004 at draft time,
  0005 claimed earlier in this same session. Re-verify at commit time
  per this repo's established practice (see ADR-0002's header comment).
-->

## Status

Proposed (design-approved via /spec interview, pre-implementation)

## Context

`mikkiola/article-pipeline`'s `scripts/sync-tooling.sh` maintains a
static, hand-written list of files to vendor from this repository.
That list already caused one silent failure: `scripts/doc_sync_tier2.py`
landed here but was not in the consumer's vendor list, and so did not
reach article-pipeline until manually caught.

**Live finding, same failure class, present today:** during this
interview, `scripts/doc_sync_tier2.py` was confirmed to exist in this
repository and to be referenced by ADR-0004 — but it is *not* listed
in README.md's "Composition (V2)" section or its file-copy
instructions. The exact bug this manifest is meant to prevent already
exists in this repository's own documentation surface, independent of
any consumer.

The owner already decided **Option A** — a manifest file living in
ToolTempest itself, read by consumer-side `sync-tooling.sh` scripts
instead of a hardcoded path list — over **Option B** (a ToolTempest-side
CI check), because Option B has a hard dependency on CI infrastructure
that does not exist yet (see ADR-0005). This choice is not reopened
here. What Option A concretely *is* was explicitly left unscoped by
that decision, which is what this ADR fixes.

## Decision

- **File:** `MANIFEST.txt` at repository root. Flat file, one
  repo-root-relative path per line.
- **Ground truth / scope:** every git-tracked file under `scripts/`,
  `schemas/`, `skills/`, `rules/` — the same four directories README's
  "Composition" section already describes. No exclusion mechanism:
  per README's own "Scope" section ("this repository is the mechanism
  only"), anything tracked in these directories is already, by
  convention, meant for consumers. No case for a repo-internal-only
  file under these paths is anticipated.
- **Completeness check:** an independent, CI-independent local script
  that compares `MANIFEST.txt` against `git ls-files` across the four
  directories above, and fails (non-zero exit) on any mismatch in
  either direction (file present on disk but missing from the
  manifest, or listed in the manifest but no longer present). Runnable
  standalone — by a developer locally, or as this repository's own
  local pre-commit hook — with no dependency on ADR-0005's CI existing.
  Once CI (ADR-0005) exists, that workflow *may* call this same script
  as one more job, but this ADR does not create that dependency, and
  the script's design must not assume a CI environment.
- **Immediate gap fix, in scope for this ADR:** `scripts/doc_sync_tier2.py`
  is added to both `MANIFEST.txt` and README.md's "Composition (V2)"
  list as part of landing this work — the manifest does not ship
  already incomplete with the exact gap it exists to prevent.
- **Explicitly out of scope:** updating article-pipeline's
  `scripts/sync-tooling.sh` to actually read `MANIFEST.txt` instead of
  its hardcoded list. That is consumer-side work, tracked as a
  separate task in `mikkiola/article-pipeline`, not in this ADR.

## Options Considered

- **Manifest mechanics — flat file / structured JSON-YAML with
  metadata / derived from README's prose:** chose flat file. A
  structured format with per-file tier metadata was considered, but a
  flat list is the simplest contract a consumer's shell script
  (`sync-tooling.sh`) can parse without a JSON/YAML dependency, and
  README's Composition prose remains the human-readable explanation of
  *why* each file matters — `MANIFEST.txt` only needs to answer
  *which* files.
- **Manifest maintenance — hand-maintained / generated or checked
  against real files:** chose checked-against-real-files (see
  Completeness check above), specifically to close the exact failure
  mode (a file landing without a corresponding list update) that
  motivated this work. A purely hand-maintained manifest would still
  be a strict improvement (single, git-diffable, ToolTempest-owned
  list) but would carry the same silent-drift risk the current
  `sync-tooling.sh` list already has.
- **Completeness-check sequencing — same workflow as ADR-0005 CI /
  independent local script:** chose independent. Making the manifest
  ADR's usefulness contingent on ADR-0005 landing first (and on CI
  infrastructure existing at all) was rejected — that is the exact
  dependency Option A was chosen over Option B to avoid.
- **Ground-truth scope — all four composition directories / only
  files README already names:** chose all four directories.
  Restricting to what README already names would have made the check
  incapable of catching the live gap this ADR discovered.

## Why

A manifest that must be checked against CI before it means anything is
not meaningfully different from Option B (rejected: hard CI
dependency). A manifest that is only checked against README's own
file list inherits README's own staleness — as demonstrated by the
live `doc_sync_tier2.py` gap. Checking against the actual git-tracked
contents of the composition directories is the only ground truth that
cannot itself silently drift the same way.

## Scope / Invariants

- This ADR governs `mikkiola/tooltempest` only: the manifest file, its
  ground truth, and its completeness-check script. It does not edit
  `mikkiola/article-pipeline`'s `sync-tooling.sh`.
- `MANIFEST.txt` and README.md's "Composition" section may diverge in
  *purpose* (machine-readable list vs. human-readable explanation) but
  must not diverge in *file set* — the completeness-check script is
  what keeps `MANIFEST.txt` honest against reality; keeping README in
  sync with `MANIFEST.txt` is a documentation-maintenance discipline
  this ADR establishes but does not mechanically enforce.

## Consequences

- `MANIFEST.txt` becomes the single machine-readable source of truth
  for what a consumer should vendor, superseding any consumer's own
  hardcoded list once that consumer adopts it (out of scope here, per
  Decision above).
- The completeness-check script becomes reusable by ADR-0005's future
  CI workflow without that workflow needing to know manifest internals.
- A follow-up task (separate from this ADR) is needed in
  `mikkiola/article-pipeline` to change `sync-tooling.sh` to read
  `MANIFEST.txt`.

## Source

`/spec` interview session, 2026-08-20, scoped to `mikkiola/tooltempest`
only. Option A vs. B decision inherited as given context, not
re-litigated. Live gap (`doc_sync_tier2.py` missing from README's
Composition list) discovered via direct repo inspection during this
session, not asserted from memory.
