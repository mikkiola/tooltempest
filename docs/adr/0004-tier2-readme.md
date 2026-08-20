# ADR-0004: Tier 2 Scope Extension — README.md as a Fourth Direct-Write Document

## Status

Accepted

## Context

ADR-0002 fixed Tier 2's target set at three documents (ARCHITECTURE.md,
BACKLOG.md, ROADMAP.md), each classified as direct-write or
human-gated by the state-vs-judgment criterion decided there. A
consuming project (`mikkiola/article-pipeline`) separately designed,
in its own DocOps SPEC.md, adding README.md to that set as a
direct-write document — the same milestone-completion reconciliation
ARCHITECTURE.md already gets, since a project's README (what it is,
how to use it) is factual state description, not a priority or
completion judgment. That design touched `scripts/doc_sync_tier2.py`,
code owned by this repository, so it was re-scoped out of
article-pipeline and filed here instead — implemented by this ADR and
the accompanying code change.

## Decision

Extend `TIER2_DOCS` to four entries and classify README.md as
direct-write, using the same state-vs-judgment criterion ADR-0002(b)
established: `("docs/ARCHITECTURE.md", "README.md", "docs/BACKLOG.md",
"docs/ROADMAP.md")`. `GATED_DOCS` is unchanged
(`{"docs/BACKLOG.md", "docs/ROADMAP.md"}`); README.md is not added to
it.

README.md's path has no `"docs/"` prefix, unlike the other three —
this is a deliberate divergence, not an inconsistency. The prefix on
the existing three reflects where those files actually live in a
consuming project (`docs/ARCHITECTURE.md`, etc.); README.md
conventionally lives at the repo root instead. The key format
convention is "repo-root-relative, matching each document's actual
location," not "always docs/-prefixed" — ADR-0002 and its
implementation happened to only have docs/-folder examples so far.

Placement within `TIER2_DOCS` matters: README.md is inserted
immediately after ARCHITECTURE.md, so both direct-write documents are
processed before either gated document. This preserves the load-bearing
invariant `apply_tier2_sync()` already depended on for a single
direct-write document — the ordering guarantee generalizes to
"direct-writes as a contiguous prefix, gated documents after" rather
than "the one direct-write document first."

CONSTITUTION.md remains untouched and out of scope, per ADR-0002 —
this ADR does not reopen that.

## Rationale

README.md describes what a project is and how to use it — the same
"what exists" factual-description role ADR-0002(b) used to justify
ARCHITECTURE.md's direct-write treatment, as distinct from BACKLOG.md/
ROADMAP.md's priority/completion judgments. No new criterion is
introduced; this is that existing criterion applied to a fourth
document, not a reconsideration of (a), (b), or (c) from ADR-0002.

## Scope / Invariants

- `TIER2_DOCS` now has four entries; `GATED_DOCS` still has exactly
  two. Any future addition to Tier 2's target set must state which
  category (direct-write or gated) it falls into using this same
  criterion, and must preserve the direct-writes-before-gated-docs
  ordering invariant.
- CONSTITUTION.md remains explicitly out of scope for Tier 2, per
  ADR-0002 — unchanged by this ADR.
- Snapshot scope (ADR-0002, Scope / Invariants) now covers four target
  files as one invocation-level set, not three.

## Consequences

- `snapshot_tier2_docs()`, `build_document_diffs()`, and
  `apply_tier2_sync()` in `scripts/doc_sync_tier2.py` operate over the
  four-entry `TIER2_DOCS` with no code change beyond the constant
  itself and docstrings referencing the old three-file set — the
  functions were already written generically over `TIER2_DOCS`/
  `GATED_DOCS` rather than hardcoding "three."
- The CLI's `--proposed` JSON key validation (`_load_proposed()`)
  rejects `"docs/README.md"` as a near-miss, same as it already
  rejected bare `"ARCHITECTURE.md"` — no new validation logic needed,
  just an additional valid key.
- Consuming projects (starting with `mikkiola/article-pipeline`, once
  it resyncs its `.tooltempest.lock` pin to a commit including this
  ADR) can now include `"README.md"` in a Tier 2 `/doc-sync`
  proposal's payload.

## Source

Follow-up to article-pipeline's consolidated DocOps SPEC.md
(2026-08-20), which designed this change but re-scoped it here since
it requires editing `tooltempest`-owned code. Implemented together
with the corresponding `scripts/doc_sync_tier2.py` change, verified
against a scratch-repo test extending the existing Tier 2 scenario set
(snapshot, diff, accept-all apply with write-order assertion, atomic
rollback including README.md, non-interactive direct-write, and CLI
key validation including a `"docs/README.md"` near-miss) — not
committed, per this repository's existing test convention (see
commits `b6a30ae`, `dca412e`, `5fb62a9`).
