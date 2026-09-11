# ADR-0010: Retire CHECKPOINT.md Support from doc_sync.py

## Status

Accepted

Supersedes the CHECKPOINT.md-handling portions of ADR-0001
(`docs/adr/0001-docops-protocol.md`) and the commit that most recently
touched them, `d35ff6859fb35bbd0e598b94ec141c0b51412881` ("fix(docops):
stop RECONCILE auto-filling missing CHECKPOINT.md fields with TODO").
ADR-0001's `inline_spec`/DETECT/VALIDATE/RECORD/STAGE contract for the
`## Milestones` pattern is unaffected and remains in effect.

## Context & Constraints

`d35ff685` changed `doc_sync.py`'s RECONCILE step so that a
`CHECKPOINT.md` block missing `verify:`/`done-when:`/`status:` blocks
the commit with a `[DRIFT]`/`[QUESTION]` pair instead of silently
writing a `TODO` placeholder — a genuine fix to the behavior ADR-0001
originally specified, fully implemented and tested against this
repository's own fixture `scripts/verify.py`.

That fix has no reachable target in any real consuming project. Each of
`mikkiola/article-pipeline`, `mikkiola/brain`, and `mikkiola/radar`
independently adopted their own project-local `ADR-0037` (dated
2026-08-20, before `d35ff685` existed) — "CHECKPOINT.md Pattern
Deprecated — Inline Milestones Only" — which removed the `"checkpoint"`
pattern from each project's own `scripts/verify.py` `classify()`
function entirely. All three now return only `"inline_spec"` or
`"UNKNOWN"`; none of them can ever hand `doc_sync.py` an entry with
`pattern == "checkpoint"`. `doc_sync.py`'s `reconcile()` only ever acted
on such entries, so from the moment each consumer adopted its own
ADR-0037, `doc_sync.py`'s CHECKPOINT.md handling became dead code —
this was true before `d35ff685` (which auto-filled a `TODO` no
`verify.py` could ever ask it to auto-fill) and remained true after it
(which blocked on a condition no `verify.py` could ever report).

This was discovered empirically, not by code inspection alone, on
2026-09-11: a live throwaway commit in both `article-pipeline` and
`brain` — a `CHECKPOINT.md` staged with a required field missing —
succeeded with exit 0 in both repositories. Each hook printed only
"N doc-owned file(s) scanned, all structurally OK," because their own
`verify.py` never inspected the file's `CHECKPOINT.md`-specific content
at all. Reading each repository's `scripts/verify.py` directly confirmed
why: `classify()` in all three is structurally identical, reduced to
`"inline_spec"` / `"UNKNOWN"` only, per each project's own ADR-0037.

`radar` never installed a `doc_sync.py`-calling hook in the first place
(its own `.git/hooks/pre-commit` documents, in its own words, that
"Radar does not consume ToolTempest's doc_sync.py") — a separate,
independent reason its CHECKPOINT.md pattern is equally unreachable
there.

## Decision

Remove CHECKPOINT.md-specific code from `scripts/doc_sync.py` entirely:
the `reconcile()` function, `find_checkpoint_missing_fields()`,
`staged_blob_text()` (unused once `reconcile()` is gone), and the
`CHECKPOINT_BLOCK_HEADING_RE`/`CHECKPOINT_REQUIRED_FIELD_RES`/
`REQUIRED_FIELD_ORDER` constants. The pre-commit lifecycle becomes
DETECT → VALIDATE → RECORD → STAGE (RECONCILE is removed as a named
step, not left in place as a no-op); `scanned`/`affected` counters are
now computed directly from DETECT's own results. `doc_sync.py` now
validates only the `inline_spec`/`## Milestones` pattern — the only
pattern any real consumer's `verify.py` can still produce.

`tests/test-doc-sync.sh`'s cases 2b (single missing field), 2c
(multiple missing fields), and 3 (staged/unstaged conflict) — which
existed specifically to test the removed `reconcile()` logic — are
removed rather than adapted. Case numbering keeps its gaps rather than
renumbering what remains. `skills/doc-sync/SKILL.md` and
`rules/drift-control.md` are updated in the same change to describe
`doc_sync.py` as validating only the `inline_spec` pattern, with no
remaining CHECKPOINT.md references.

## Alternatives & Rationale

**Reintroduce `"checkpoint"` pattern recognition into each consumer's
own `verify.py`** — rejected. Each of the three consumers adopted its
own ADR-0037 independently and for its own stated reasons (a
CHECKPOINT.md's mere presence could silently override a SPEC.md's own,
better-maintained inline Milestones section — see `article-pipeline`'s
ADR-0037 for the specific incident that motivated it). Reversing that
from this repository would contradict a decision each consumer already
made and owns; ToolTempest has no standing to override a consumer's own
project-local ADR from the outside, and doing so would resurrect the
exact silent-override risk ADR-0037 was written to eliminate.

**Leave the dead code in place** — rejected. `d35ff685`'s fix is
correctly implemented and passes every test written against it, but a
correct implementation of unreachable logic is still unreachable logic:
it cannot be exercised by any real consumer today, adds maintenance
surface (a second pattern to keep in sync with `verify.py`'s own
contract, a second set of test scenarios, a second block of
documentation) for a code path nothing can ever invoke, and invites a
future reader to assume it does something in a live repository, which
this session's own empirical test disproved.

**Remove CHECKPOINT.md support entirely, this record** — chosen. Matches
the reality confirmed by direct testing in two real consumers: no
project this repository serves can produce a `"checkpoint"`-pattern
entry any more. Removing the code is not a reversal of `d35ff685`'s
DRIFT+QUESTION design choice — that was the right fix for the behavior
ADR-0001 specified, and would still be the right fix if any consumer's
`verify.py` reported the pattern DocOps was built to look for. It never
had a live target to fix.

## Consequences

- `scripts/doc_sync.py` only ever validates the `inline_spec` pattern
  now. Any project wanting a future, richer per-block field format
  (`verify:`/`done-when:`/`status:`, structurally distinct from a flat
  `## Milestones` checkbox line) needs a new design, not a revival of
  this one — see `article-pipeline`'s own ADR-0037, "Reversal
  condition," for that project's parallel note on the same point.
- `.tempest/runs/docops_<run_id>.json` audit records no longer carry any
  meaning tied to a `"checkpoint"` pattern; `counters.updated` remains
  present in the schema (unchanged by this record) and stays `0` in
  every record, since nothing in the remaining code path ever writes to
  a doc-owned file.
- No consumer's `.tooltempest.lock`, vendored `scripts/doc_sync.py`
  copy, or `scripts/verify.py` is touched by this record. Vendoring this
  change out to `brain`, `article-pipeline`, and `radar` is a separate,
  later step, same as any other ToolTempest version bump.
- `docs/adr/0001-docops-protocol.md` is not edited by this record — its
  CHECKPOINT.md-specific text becomes historical (describing what V2
  originally shipped, and why), superseded by this ADR for current
  behavior, rather than rewritten in place.

## Confirmation & Revisit

Confirmed by direct, empirical testing, not code inspection alone: a
real throwaway commit with a `CHECKPOINT.md` missing a required field,
attempted for real (no `--no-verify`) in both `mikkiola/article-pipeline`
and `mikkiola/brain` on 2026-09-11, succeeded in both — proving the
"checkpoint" pattern is unreachable in both of the only two consumers
that actually invoke `doc_sync.py` today. `tests/test-doc-sync.sh`'s
remaining cases (1, 2, 4, 5, 6a, 6b, 7a, 7b) all pass against the
CHECKPOINT.md-free implementation.

Revisit if a future consumer project deliberately reintroduces a
`"checkpoint"`-equivalent pattern into its own `verify.py` with a
genuine need for richer per-block fields than a `## Milestones`
checkbox line carries — at that point, restoring (or redesigning)
structural support in `doc_sync.py` would be a new record weighing that
specific, real need, not a revert of this one.

## Source

Owner decision, 2026-09-11, following this session's own empirical
discovery (live commit testing in `article-pipeline` and `brain`) that
`d35ff685`'s CHECKPOINT.md fix, though correctly implemented, has no
reachable target in any consuming project.
