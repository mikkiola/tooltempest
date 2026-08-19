# ADR-0003: Tier 2 Confirmation Granularity — Per-File, Not Per-Line

Status: Accepted
Supersedes: Clarifies wording in ADR-0002(a) and its Implementation
Constraints section. Does not change decisions (a), (b), or (c).

## Context

ADR-0002 describes the Tier 2 confirmation mechanism using the phrase
"line-by-line" (decision a) and "individual line-level diff... accepts/
rejects before it is written" (Implementation Constraints). Read
literally, this describes per-line accept/reject granularity: a human
could accept one changed line in BACKLOG.md and reject another within
the same file, producing a partially-applied write.

During Stage 3 implementation, this reading was found incompatible
with the snapshot-restore primitive ADR-0002(a) itself mandates
reusing: `restore_snapshots()` (tooltempest `scripts/doc_sync.py`)
restores a file to a single prior snapshot state — it has no concept
of partial, per-hunk application. A per-line confirmation unit and a
per-file restore unit cannot both hold without a third mechanism
(partial diff hunk application) that ADR-0002 never specifies as a
requirement.

## Decision

Confirmation granularity is per-file, not per-line. For BACKLOG.md and
ROADMAP.md, the human is shown the full line-level diff (so every
changed line is visible before deciding — this satisfies the
"line-by-line" visibility intent) and makes ONE accept/reject decision
covering that file's entire proposed diff. There is no partial
application of a subset of lines within a file.

"Line-by-line" in ADR-0002 is read as describing the granularity of
what is *displayed* to the human (full line-level diff, not a
summarized description of the change), not the granularity of the
*decision*. The decision unit is the file.

## Consequences

- `apply_tier2_sync()` (Stage 3) implements one prompt per gated file,
  full diff shown, single accept/reject.
- Rollback remains atomic per invocation (unchanged from ADR-0002a),
  consistent with `restore_snapshots()`'s existing file-level
  semantics.
- If per-line/per-hunk partial application is later found necessary,
  it requires a new ADR and a new restore primitive — not a
  reinterpretation of this one.
