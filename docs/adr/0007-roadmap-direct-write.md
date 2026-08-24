# ADR-0007: ROADMAP.md Reclassified as Direct-Write

## Status

Accepted

## Context & Constraints

ADR-0002(b) classified ROADMAP.md as gated (requires human confirmation
before write), on the same "judgment, not state" basis as BACKLOG.md.
`GATED_DOCS` in `scripts/doc_sync_tier2.py` hardcoded this:
`frozenset({"docs/BACKLOG.md", "docs/ROADMAP.md"})`, with no per-call
override — `apply_tier2_sync(interactive=False)` unconditionally raises
`RuntimeError` if `proposed` contains `"docs/ROADMAP.md"`.

`mikkiola/article-pipeline`, this tooling's one real consumer today,
has separately decided its own `docs/ROADMAP.md` should get
direct-write treatment instead — the same treatment
`ARCHITECTURE.md`/`README.md` already get here (`README.md` via
ADR-0004, on the same state-vs-judgment criterion). This mirrors how
ADR-0004 itself arrived: a consumer-side design touching
`tooltempest`-owned code, re-scoped into this repository rather than
forked into the consumer.

Constraint: this repository has exactly one real consumer
(article-pipeline) and no others today. This decision is scoped to
that fact, not to a hypothetical future where different consumers
might want different gating for ROADMAP.md.

## Decision

Remove `"docs/ROADMAP.md"` from `GATED_DOCS`, leaving
`GATED_DOCS = frozenset({"docs/BACKLOG.md"})`. `TIER2_DOCS` is
unchanged — ROADMAP.md remains a Tier 2 target, still written through
the same snapshot/diff/apply path as every other document, just
without the confirmation gate. BACKLOG.md's gating is unchanged and
out of scope for this ADR.

No parameterization (env var, config flag, per-call override) is added
to make gating configurable per consumer. `GATED_DOCS` stays a single
hardcoded module-level constant, edited directly, as it was for
ADR-0002 and ADR-0004.

## Alternatives & Rationale

**Parameterize `GATED_DOCS` per caller/consumer** (e.g. an
`extra_direct_write` argument to `apply_tier2_sync()`, or a config file
consumers supply) — rejected. This repository has one real consumer;
building a mechanism to let gating vary per consumer solves a problem
that does not exist yet. If a second consumer later wants BACKLOG.md
gated but not ROADMAP.md, or vice versa, that is grounds for a new ADR
introducing parameterization then, informed by an actual second
requirement instead of a guessed one.

**Leave ROADMAP.md gated and have article-pipeline route its
`/doc-sync` proposals for ROADMAP.md through the interactive path
only** — rejected. This preserves the constant unchanged but does not
give article-pipeline what it decided it wants (unattended
reconciliation of ROADMAP.md alongside the other direct-write docs);
it would just relocate the workaround into the consumer without fixing
the actual constraint.

**Reclassify ROADMAP.md the same way ADR-0004 reclassified README.md**
(edit the hardcoded set directly, in this repository, since the code
is owned here) — chosen. Same shape of decision as ADR-0004: a
consumer-driven design change to Tier 2 classification, implemented
where the code lives, on the existing state-vs-judgment criterion
inherited from ADR-0002(b) without reopening it.

## Consequences

- `apply_tier2_sync(interactive=False)` no longer raises `RuntimeError`
  when `proposed` contains `"docs/ROADMAP.md"`; it writes ROADMAP.md
  directly, same as ARCHITECTURE.md and README.md.
- `apply_tier2_sync(interactive=True)` no longer prompts for ROADMAP.md
  before writing it.
- `GATED_DOCS` now has exactly one member (`"docs/BACKLOG.md"`).
  BACKLOG.md's confirmation requirement, and the RuntimeError guard for
  it in non-interactive mode, are unchanged.
- `TIER2_DOCS` order (`ARCHITECTURE.md`, `README.md`, `BACKLOG.md`,
  `ROADMAP.md`) is unchanged and is not reordered by this ADR. This
  means the "direct-write documents form a contiguous prefix before
  any gated document" phrasing ADR-0004 used to describe the ordering
  invariant no longer holds literally — ROADMAP.md, now direct-write,
  still sits after the gated BACKLOG.md in processing order. This is
  functionally harmless (a BACKLOG.md rejection rolls back only what
  was already written before it — ARCHITECTURE.md and README.md —
  and ROADMAP.md is not reached until its own turn), and is called out
  here rather than silently left inconsistent with ADR-0004's text.
  Reordering `TIER2_DOCS` itself was considered and deliberately left
  out of this ADR's scope.
- Once article-pipeline resyncs its `.tooltempest.lock` pin to a commit
  including this ADR, its `/doc-sync` proposals can include
  `"docs/ROADMAP.md"` in a non-interactive `apply_tier2_sync()` call
  without triggering the old `RuntimeError`. That resync is
  article-pipeline's own follow-up, not part of this change.

## Confirmation & Revisit

Confirmed by the scratch-repo smoke check run against this commit: a
non-interactive `apply_tier2_sync()` call proposing changes to
`docs/ARCHITECTURE.md`, `README.md`, and `docs/ROADMAP.md` together
applies all three without prompting (`status: "applied"`); a
non-interactive call proposing `docs/BACKLOG.md` still raises
`RuntimeError`. Not committed, per this repository's existing test
convention (see ADR-0004's Source section and commits `b6a30ae`,
`dca412e`, `5fb62a9`).

Revisit if a second real consumer of this tooling emerges wanting
different gating for ROADMAP.md than article-pipeline — that is the
trigger for reconsidering parameterization, not a reason to add it
now.

Source: article-pipeline's own DocOps design decision to treat
`docs/ROADMAP.md` as direct-write (2026-08-24), re-scoped here since it
requires editing `tooltempest`-owned code, following the same pattern
ADR-0004 established for README.md.
