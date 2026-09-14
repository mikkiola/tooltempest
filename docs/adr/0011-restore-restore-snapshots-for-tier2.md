# ADR-0011: Restore `restore_snapshots()` in `doc_sync.py`, Corrected for Tier 2

## Status

Accepted

Corrects an unintended side effect of commit
`d35ff6859fb35bbd0e598b94ec141c0b51412881` ("fix(docops): stop
RECONCILE auto-filling missing CHECKPOINT.md fields with TODO") — not
a reversal of ADR-0010 (`docs/adr/0010-retire-checkpoint-md-support.md`).
CHECKPOINT.md support stays retired; ADR-0010's decision is unaffected
by this record.

## Context & Constraints

`scripts/doc_sync_tier2.py` imports `restore_snapshots` from
`scripts/doc_sync.py` (line 42) and calls it at line 241, inside
`apply_tier2_sync()`'s rollback path — the mechanism that undoes
partially-written Tier 2 gated-doc changes (e.g. `docs/ARCHITECTURE.md`,
`docs/BACKLOG.md`) on a gated-document rejection or any exception. This
is load-bearing, not incidental: `__all__` exports it (line 50), two
docstrings describe it (lines 81, 179), and it is the only rollback
mechanism `apply_tier2_sync()` has.

`restore_snapshots()` was removed from `doc_sync.py` by commit
`d35ff6859fb35bbd0e598b94ec141c0b51412881` ("fix(docops): stop
RECONCILE auto-filling missing CHECKPOINT.md fields with TODO",
2026-09-11 11:40:47) — confirmed directly: `git show d35ff68 --
scripts/doc_sync.py` shows its removal (`-def
restore_snapshots(...)`, `-        restore_snapshots(root,
result["snapshots"], result["modified"])`), and `git show
d1a9d0a:scripts/doc_sync.py` (the original DocOps Protocol commit,
ADR-0001) confirms it existed before that. `d35ff68` removed it
because, at that point, RECONCILE no longer wrote a `TODO` placeholder
to auto-fill a missing CHECKPOINT.md field — so RECONCILE itself had
nothing left to roll back, and `restore_snapshots()` looked unreachable
from inside `doc_sync.py`'s own file. ADR-0010 (2h24m later the same
day) removed the rest of the CHECKPOINT.md/RECONCILE machinery
(`reconcile()`, `find_checkpoint_missing_fields()`, `staged_blob_text()`,
the CHECKPOINT-specific regex constants) — by which point
`restore_snapshots()` was already gone; ADR-0010's own diff never
touches it, confirmed by reading `git show d337fad -- scripts/doc_sync.py`
directly.

Neither commit's message or diff shows any awareness that
`scripts/doc_sync_tier2.py` — a separate module, unrelated to
CHECKPOINT.md, implementing an entirely different rollback need
(Tier 2's own gated-doc apply/rollback, per ADR-0002/ADR-0003) —
imports this exact function by name. `restore_snapshots()`'s own
pre-removal implementation (`git show d1a9d0a:scripts/doc_sync.py`)
carried zero CHECKPOINT.md-specific logic: it is a generic "rewrite
each modified file back to its pre-modification snapshot, or delete it
if it didn't exist before" utility, operating purely on a
`{relative_path: (existed, original)}` snapshot dict and a list of
modified paths — nothing about CHECKPOINT.md, RECONCILE, or any
parsing logic lives inside the function body itself. Its docstring's
own historical wording ("pre-RECONCILE bytes") described *why* it used
to be called, not what it does.

Confirmed broken in practice, not just by code reading: `python3 -c
"import sys; sys.path.insert(0, 'scripts'); import doc_sync_tier2"`
raised `ImportError: cannot import name 'restore_snapshots' from
'doc_sync'` at the pinned commit, before this fix. This was found via
`mikkiola/article-pipeline`'s own `docs/BACKLOG.md` `[B-061]`, filed
after `.github/scripts/reconcile.py`'s own tests
(`test_reconcile_error_path.py`, `test-reconcile.sh`) failed with this
same error — `reconcile.py` imports `apply_tier2_sync` from
`doc_sync_tier2`, which failed to import at all as a result.

## Decision

Restore `restore_snapshots(root: Path, snapshots: dict, modified:
list[str]) -> None` in `scripts/doc_sync.py`, verbatim in behavior
(same signature, same body — copied from its last-known-good version
at commit `d1a9d0a`, before `d35ff68` removed it), with only its
docstring updated: RECONCILE-specific wording ("pre-RECONCILE bytes")
is replaced with generic wording, and a note explains why the function
lives in `doc_sync.py` despite Tier 1 no longer calling it itself — as
a shared primitive `doc_sync_tier2.py` depends on, the same way both
modules already share `relative_to_root()`/`repo_root()`.

The module-level docstring's design-notes bullet ("doc_sync.py never
writes to any doc-owned file... there is nothing to snapshot or
restore") is also corrected to scope that claim to Tier 1's own flow
specifically, rather than reading as a blanket claim about the whole
module — which is exactly the ambiguity that made this regression easy
to introduce and hard to notice in `d35ff68`'s diff.

No CHECKPOINT.md-specific code is reintroduced. `reconcile()`,
`find_checkpoint_missing_fields()`, `staged_blob_text()`, and the
CHECKPOINT-specific constants ADR-0010 removed remain removed.

## Alternatives & Rationale

| Option | Rationale for outcome |
|---|---|
| A. Restore `restore_snapshots()` in `doc_sync.py`, generic, docstring-corrected (chosen) | The function itself was already fully generic before removal — nothing to genericize, only to restore and re-document. Matches its actual only real consumer's expectation (`doc_sync_tier2.py`'s import line, `__all__`, and two call sites are already written against this exact signature) with zero changes needed on the Tier 2 side. |
| B. Move a genericized version of the function into `doc_sync_tier2.py` itself, since that's its only real consumer today | Rejected: would require changing `doc_sync_tier2.py`'s import line and its `__all__` re-export, for no functional gain — the function was never CHECKPOINT.md-specific to begin with, so there is nothing to "genericize" that restoring it in place doesn't already achieve. Moving it would also break the shared-utility placement precedent this repo already established (Tier 2 already imports plain utilities like `relative_to_root()`/`repo_root()` from `doc_sync.py` rather than duplicating them). |
| C. Leave `restore_snapshots()` removed and have `doc_sync_tier2.py` implement its own independent rollback logic | Rejected: duplicates working logic for no reason, when the original is already the literal, git-history-verified working implementation — reimplementing it from scratch risks a subtle behavioral drift (e.g. the existed/original tuple-unpacking order, the delete-if-didn't-exist branch) that a verbatim restore avoids entirely. |

A.

## Consequences

- `scripts/doc_sync_tier2.py`'s `apply_tier2_sync()` rollback path is
  restored to working order — confirmed by direct import and by this
  repo's own test suite (see Confirmation & Revisit below).
- `.github/scripts/reconcile.py` and `adr-0033-reconciliation.yml` in
  `mikkiola/article-pipeline` (the consumer that surfaced this bug,
  `[B-061]`) will need `.tooltempest.lock` bumped to this fix's commit,
  and the two vendored files re-synced, before they're unblocked — that
  bump is a separate, later step in that repository, not part of this
  record.
- ADR-0010's decision is unchanged: `scripts/doc_sync.py` still
  validates only the `inline_spec`/`## Milestones` pattern; no
  CHECKPOINT.md-specific code returns.
- A structural gap this incident exposes, not fixed here: nothing in
  this repository's own tests would have caught a consumer-facing
  import break like this one, since `tests/test-doc-sync.sh` only
  exercises `doc_sync.py`'s own CLI entry points, never
  `doc_sync_tier2.py`'s import of it. Worth a future item (not scoped
  here) to add a minimal cross-module import/smoke test that would
  have caught this at the moment `d35ff68` removed the function.

## Confirmation & Revisit

Confirmed directly: `python3 -c "import sys; sys.path.insert(0,
'scripts'); import doc_sync_tier2"` now exits with no output/error
(previously raised `ImportError`). `bash tests/test-doc-sync.sh` passes
against the restored implementation. The restoration was verified
against `restore_snapshots()`'s actual last-known-good implementation
(`git show d1a9d0a:scripts/doc_sync.py`), not a reimplementation from
memory or assumption.

Revisit if `doc_sync_tier2.py`'s snapshot/rollback need ever diverges
from what this generic function provides (e.g. a future Tier 2 design
needing partial, per-file rollback ordering this function doesn't
support) — at that point, a new record redesigns the primitive, not a
revert of this one.

## Source

`mikkiola/article-pipeline` session investigating apparent missing
Collector reports surfaced a broader gap-analysis pass, 2026-09-14,
which found this `ImportError` and filed that repository's own
`docs/BACKLOG.md` `[B-061]`. This record is the upstream fix that
entry's own text names as the required next step.
