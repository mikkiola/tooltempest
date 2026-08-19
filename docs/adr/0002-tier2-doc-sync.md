# ADR-0002: Tier 2 /doc-sync Milestone Documentation Synchronization

<!--
  REPOSITORY: mikkiola/tooltempest (docs/adr/0002-tier2-doc-sync.md), NOT
  article-pipeline. /doc-sync is DocOps tooling — same repo, same
  namespace as ADR-0001 (Tier 1 protocol). article-pipeline remains the
  first *consumer* of Tier 2, same relationship it already has to Tier 1;
  it is not where the decision record lives.

  Number confirmed 2026-08-19: ToolTempest's docs/adr/ currently holds
  only 0001-docops-protocol.md. This is the next free number in
  ToolTempest's own sequence (0032 was considered and rejected — that
  number belongs to article-pipeline's separate, unrelated sequence,
  where 0031 is occupied by an unrelated D-025 experiment ADR; conflating
  the two repos' numbering was the exact mistake corrected during ADR-0001
  itself, see that ADR's Source section).
  Claude Code should still re-check `ls docs/adr/` in ToolTempest
  immediately before commit in case a newer ADR landed since this draft.
-->

## Status

Proposed (design-approved, pre-implementation)

## Context

Tier 1 (V2.0, ADR-0001 in ToolTempest) covers structural, deterministic,
pre-commit/pre-push synchronization of CHECKPOINT.md/SPEC.md. It does not
touch ARCHITECTURE.md, BACKLOG.md, or ROADMAP.md, and does not run on
milestone completion.

Tier 2 is a separately-invoked skill (`/doc-sync`), not a git hook, not run
on every commit. It runs when a milestone closes, to reconcile three
canonical docs against actual repository state.

Three design questions were open before implementation could start:

- (a) physical mechanism for proposing changes to the human
- (b) whether trust level should be uniform across the three documents, or
  differ by document
- (c) who initiates the /doc-sync invocation

## Decision

**(a) Mechanism:** Interactive line-by-line diff confirmation (human sees
each proposed change, accepts/rejects), not an out-of-band PR/diff artifact.
A snapshot is taken once per `/doc-sync` invocation (covering all three
target files together), not per individual line change. On interruption or
rejection mid-run, restore from that invocation-level snapshot.

**(b) Trust level per document — asymmetric, not uniform:**

| Document | Mode | Basis |
|---|---|---|
| ARCHITECTURE.md | Direct update | Describes system state ("what exists"). This is a factual description the agent is positioned to author directly. |
| BACKLOG.md | Proposal (🔄), requires human confirmation | Encodes a judgment about priority. Priority is the owner's call by definition. |
| ROADMAP.md | Proposal (🔄), requires human confirmation | Encodes a judgment about what counts as progress/completion. Same reasoning as BACKLOG.md. |

The asymmetry is justified by the **role each document plays**, not by how
technically deterministic any single diff operation happens to be. This
was explicitly considered and rejected as the basis (see Rejected Options)
in favor of the simpler, more durable criterion: state vs. judgment.

**(c) Initiation:** The agent decides autonomously when a milestone has
closed and invokes `/doc-sync` itself, without waiting for an explicit
human command each time. This is not a new decision — it is a direct
continuation of the autonomy principle already established in
article-pipeline's CONSTITUTION.md (commits 8ea207b/1e354bc/5f2f35a/
6c750f5, 2026-08-18). That principle is cited here as precedent from
this ADR's first consumer, not restated as a ToolTempest-native rule —
ToolTempest itself does not currently have an equivalent constitution
document; if ToolTempest gains other consumers with different autonomy
norms, this cross-repo citation may need revisiting. The narrow
stop-case remains the same one already defined there: "genuinely
different outcomes, no basis to choose" — not "this is architectural"
as a category on its own.

## Rationale

**(a):** Evaluated against a rejected alternative (agent writes changes,
opens a separate diff/PR for review). Line-by-line confirmation:
- reuses the existing Tier 1 snapshot-restore primitive (Safe Isolation) —
  no new mechanism required
- keeps the feedback loop short — matches the trust pattern already
  established for Tier 1 (agent acts, human can intervene immediately),
  rather than introducing a second, slower-cadence trust model inside the
  same project
- avoids the asymmetric-risk failure mode of a PR-based flow: an
  unreviewed or rubber-stamped PR after a quiet week is a worse failure
  than a rejected line in an interactive session

**(b):** The asymmetry in the owner's original proposal
(ARCHITECTURE.md = ✅, BACKLOG.md/ROADMAP.md = 🔄) initially looked
inconsistent under one trigger ("major code change"). Two ways to resolve
that inconsistency were considered:
- ground the asymmetry in whether the specific diff operation is
  mechanically deterministic (rejected — see below)
- ground it in the document's role (chosen)

Role-based justification does not depend on implementation detail and
applies identically regardless of how complex any given diff turns out to
be. It also matches Tier 1 precedent: CONSTITUTION.md is untouchable even
by Tier 2 — document role, not operation complexity, is already the
project's established axis for deciding who may write where.

**(c):** No new rationale — inherits CONSTITUTION.md's existing default.

## Scope / Invariants

- CONSTITUTION.md is out of scope for Tier 2 entirely — no direct update,
  no proposal. Only a human edits it.
- Tier 2 does not run inside git hook lifecycle (pre-commit/pre-push).
  Network access, non-determinism, and latency constraints that apply to
  Tier 1 do not apply here — this is the explicit reason Tier 2 is
  permitted to differ from the Tier 1 "no AI inside hooks" anti-pattern
  rejection. Tier 2 living outside the hook lifecycle is the distinguishing
  fact; it is not a reopening of that earlier rejection.
- Tier 2 is invoked on milestone completion, not on every commit.
- Snapshot scope: the three target files (ARCHITECTURE.md, BACKLOG.md,
  ROADMAP.md) as a set, taken once at invocation start.

## Implementation Constraints

- ARCHITECTURE.md writes: direct, no confirmation step, but still produced
  via the same diff-generation path as the other two (no separate code path
  that skips review-ability of the change itself — "direct" means no human
  gate, not "unlogged").
- BACKLOG.md / ROADMAP.md writes: each proposed change surfaced as an
  individual line-level diff, human accepts/rejects before it is written.
- All three: covered by the single invocation-level snapshot; a rejected
  or interrupted run restores all three to pre-invocation state, not just
  the file being edited when interruption occurred.
- Audit trail for Tier 2 runs: follow the same commit-artifact pattern as
  Tier 1 (ADR-0001, ToolTempest) unless a gap is found that requires a new
  decision — do not invent a new audit mechanism without flagging it back
  to the architect first.

## Rejected Options

- **PR-based proposal mechanism (for a):** rejected — requires a new
  primitive outside git's commit-time PR flow (Tier 2 is not commit-time),
  which is explicitly out of scope per this session's "not building for a
  second consumer / not over-generalizing" boundary.
- **Uniform 🔄-proposal for all three documents (for b):** considered as
  the "safe symmetric default" but rejected once the owner reframed the
  question — the asymmetry was never really about which document is
  *harder to get right mechanically*, it was about which document
  encodes state versus judgment. Forcing symmetry would have been
  symmetry for its own sake, not a principled simplification.
- **Determinism-of-operation as the basis for (b):** considered (grep-gate
  proposal: check whether doc_sync.py's Tier 1 logic already treats some
  ARCHITECTURE.md-like section as structurally derivable) — rejected in
  favor of the role-based criterion, which does not require this check and
  is stable regardless of implementation detail.

## Consequences

- Implementation of `/doc-sync` must branch its write path by document
  (direct vs. proposal), not treat all three uniformly.
- No second ADR is expected after implementation lands — this ADR is the
  contract; post-implementation work is verification (does the code match
  this ADR), not a new decision record, unless implementation surfaces a
  genuine architectural question not covered here.
- If a future session wants to change (a), (b), or (c), that is a new,
  superseding ADR — this one is not edited in place per Immutable Lineage.

## Reversal Condition

If, after a week of real Tier 2 usage, the line-by-line confirmation proves
too slow/noisy for ARCHITECTURE.md-scale changes, or the BACKLOG/ROADMAP
proposal flow is routinely rubber-stamped without real review (defeating
the purpose of (b)), that is grounds to revisit — via a new ADR, not an
edit to this one.

## Source

Session: DocOps Protocol V2.0 hardening + Tier 2 /doc-sync architecture,
2026-08-19. Decisions (a) and (b) closed via decision-analysis (Taleb /
systems-loops / Harari / engineering-fact lenses) in architect chat;
(c) inherited from CONSTITUTION.md commits 8ea207b/1e354bc/5f2f35a/6c750f5
(2026-08-18) in article-pipeline — cited here as precedent, not as this
ADR's home repository. This ADR belongs to mikkiola/tooltempest, same
repo as ADR-0001 (Tier 1). Number 0002 confirmed against ToolTempest's
docs/adr/ (only 0001 present) at draft time — re-verify at commit time.
