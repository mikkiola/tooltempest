# ADR-0012: docs/PROJECT.md as a Required Sixth Canonical Document Type, for Tracked Project Repositories

## Status

Accepted

## Context & Constraints

Owner decision, 2026-09-18: every project in this ecosystem should
work "как близнецы" — sharing one canonical document structure, not a
per-repository ad hoc one. `mikkiola/article-pipeline` recently added
`docs/PROJECT.md` — a system-level document stating why the system
exists, what it can prove about itself by its own operation, a
top-level current-vs-target framing, and a channel-agnostic Definition
of Done — sitting between `docs/CONSTITUTION.md` and
`docs/ARCHITECTURE.md` in its own session-start reading order. No
other repository in the ecosystem had this document at the time this
was checked (confirmed absent from `radar`, `brain`, `tooltempest`,
`collector`, `analyzer`).

`docs/reference/documentation-rules.md` (this repository's own
ecosystem-wide document-structure standard) did not yet name
`PROJECT.md` as a canonical type — it named five: `CONSTITUTION.md`,
`ARCHITECTURE.md`, `ROADMAP.md`, `BACKLOG.md`, `docs/adr/` (plus the
generated `ADR-INDEX.md`).

A real constraint surfaced during this same session, before this
decision was finalized: this ecosystem already has a standing, stated
split between "tracked projects" and "tools/infra", established in
`mikkiola/analyzer`'s own `SPEC.md` (confirmed directly:
`analyzer/SPEC.md`'s "Goals" section tracks goals for
`article-pipeline`, `brain`, `radar` (via each project's own
`docs/ROADMAP.md` Current Pointer) and `archi-kg` (goal-missing but
still tracked); its "Governance model" section states `analyzer`
itself follows Collector's lightweight model specifically because it
is "a small, single-purpose tool consuming Collector's output" with no
goal of its own to track — `CONSTITUTION.md` + `SPEC.md` at repo root
only, no `ARCHITECTURE.md`/`ROADMAP.md`/`BACKLOG.md`/`docs/adr/`, the
full DocOps stack being disproportionate governance overhead for a
repo with no system-level goal of its own). `tooltempest` (this
repository) is the shared tooling mechanism every consumer points to —
infrastructure, not a goal-tracked project — even though it happens to
carry a full `docs/adr/` for its own engineering history; it does not
publish content, has no "system outcome" of its own to state, and is
not one of `analyzer`'s tracked projects.

A first draft of this decision proposed naming `PROJECT.md` a required
document for "every project in this ecosystem" without qualification —
caught before being committed: it would have applied a system-level
goal/outcome document to repositories (ToolTempest, Collector,
Analyzer, radar-vault) that structurally have no such goal, directly
contradicting the split `analyzer`'s own `SPEC.md` already establishes,
rather than mirroring it.

## Decision

`docs/PROJECT.md` becomes a required sixth canonical document type in
`docs/reference/documentation-rules.md`, inserted between
`CONSTITUTION.md` and `ARCHITECTURE.md` in the chain of authority, but
scoped to **tracked project repositories only**: `article-pipeline`,
`Brain`, `Radar`, `Archi-kg` — the same repositories `analyzer`'s own
`SPEC.md` already tracks goals for. Infrastructure/tooling
repositories (`ToolTempest`, `Collector`, `Analyzer`, `radar-vault`)
are explicitly out of scope for this new document type, on the same
reasoning `analyzer`'s own `SPEC.md` already gives for excluding them
from goal tracking generally: a small, single-purpose tool consuming
another repo's output has no system-level goal or outcome of its own
to state. `docs/reference/documentation-rules.md`'s own new "2.
PROJECT.md" section states this applicability split explicitly, as its
own "Applies to" line, rather than leaving it implicit.

article-pipeline's actual `docs/PROJECT.md` is the reference example
for this document type's structure: System Goal, System Outcome (the
single, narrowest claim the system can actually prove about itself),
Current vs. Target (top-level), Definition of Done
(channel/component-agnostic), and boundary statements naming adjacent
or depended-on systems this repository does not implement, track, or
duplicate.

## Alternatives & Rationale

| Option | Rationale for outcome |
|---|---|
| A. Required sixth canonical document, scoped to tracked-project repositories only (chosen) | Matches the owner's "как близнецы" intent for the repositories that actually share a comparable shape (a goal-tracked project with its own system-level outcome), while respecting the ecosystem's own pre-existing project/tool split (`analyzer`'s `SPEC.md`) instead of contradicting it. |
| B. Required sixth canonical document for every ecosystem repository, unqualified | Rejected — an earlier draft of this decision, caught before commit. Would apply a system-level goal/outcome document to tools/infra repositories that structurally have no such goal (ToolTempest, Collector, Analyzer, radar-vault already justify skipping the full canonical stack for exactly this reason), directly contradicting `analyzer`'s own `SPEC.md` classification rather than mirroring it. |
| C. Leave `docs/PROJECT.md` as an article-pipeline-only addition, not part of the shared standard | Rejected — breaks structural uniformity across the repositories that do share this shape (the tracked projects), which is the actual owner-stated goal ("как близнецы"); article-pipeline having one and Brain/Radar/Archi-kg lacking the equivalent is exactly the drift the shared standard exists to prevent. |
| D. Fold `PROJECT.md`'s content into each tracked project's existing `ARCHITECTURE.md` as a preamble | Rejected — `ARCHITECTURE.md`'s own stated purpose in `documentation-rules.md` is current state only ("what exists right now... nothing else"); mixing in system-level goal/outcome content would violate this same document's own single-source-of-truth principle. |

A, scoped as stated.

## Consequences

- `docs/reference/documentation-rules.md` names `docs/PROJECT.md` as a
  required sixth canonical document type, scoped to tracked-project
  repositories, with its own Applies-to/Purpose/What-must-NOT-be-here/
  Structure/How-to-maintain/Rules-for-edits sections, and renumbers the
  following sections (`ARCHITECTURE.md` → 3, `ROADMAP.md` → 4,
  `BACKLOG.md` → 5, `docs/adr/` → 6, `ADR-INDEX.md` → 7).
- Every **tracked project's** own `docs/CONSTITUTION.md` must
  eventually be updated to include `PROJECT.md` in its session-start
  reading order — `article-pipeline`'s is already done; `Brain`,
  `Radar`, and `Archi-kg` are not, and each needs its own
  `docs/PROJECT.md` created — that creation is separate, future,
  per-project work, not part of this record.
- `ToolTempest`, `Collector`, `Analyzer`, and `radar-vault` need no
  `docs/PROJECT.md` and no `CONSTITUTION.md` reading-order change as a
  result of this decision — explicitly not required, not merely
  deferred.
- If a tools/infra repository is later reclassified as a tracked
  project (gains a system-level goal of its own), that reclassification
  — not this record — is what would trigger adding it a
  `docs/PROJECT.md`.

## Confirmation & Revisit

Confirmed directly against the actual repositories, not assumed: read
`article-pipeline`'s actual `docs/PROJECT.md` in full before writing
the new section (its System Goal/System Outcome/Current vs.
Target/Definition of Done/Boundary structure is what the new section's
"Structure" subsection is based on); read `analyzer`'s actual
`SPEC.md` directly (its "Goals" and "Governance model" sections) to
confirm the tracked-project/tools-infra split and its membership,
rather than assuming the classification's exact wording as given
mid-session; confirmed `docs/PROJECT.md` is absent from `radar`,
`brain`, `tooltempest`, `collector`, and `analyzer` by direct listing.

Revisit if `analyzer`'s own tracked-project/tools-infra classification
changes (a repository is promoted or demoted), or if a tools/infra
repository is given a system-level goal of its own — at that point a
new record updates this document's scope, not a silent edit to this
one (Immutable Lineage).

## Source

Owner decision, 2026-09-18 ("все проекты должны работать как
близнецы"), executed in a `mikkiola/tooltempest` session updating
`docs/reference/documentation-rules.md` on `mikkiola/article-pipeline`'s
behalf. Corrected mid-session, before any commit: the owner flagged
that PROJECT.md is required only for tracked projects, not
infrastructure/tooling repositories, citing the pre-existing split
already stated in `mikkiola/analyzer`'s own `SPEC.md`. This record
incorporates that correction directly, rather than committing the
unqualified version and superseding it afterward.
