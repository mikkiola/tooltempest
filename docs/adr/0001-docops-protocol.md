# 0001 — DocOps Protocol: automated pre-commit reconciliation and pre-push hard validation

## Status
ACTIVE. Specifies `scripts/doc_sync.py`, `schemas/execution-record.schema.json`,
and `skills/doc-sync/SKILL.md`, introduced in this same record's commit.
Composition bumped from V1 (three markdown primitives) to V2 — see
README.md "Composition (V2)".

## Decision
A consuming project may wire two of its local git hooks —
`.git/hooks/pre-commit` and `.git/hooks/pre-push` — to call
`scripts/doc_sync.py`, a client-agnostic script delivered as part of
ToolTempest. The script keeps a project's doc-owned files (`SPEC.md`,
`CHECKPOINT.md`) structurally consistent at commit time and enforces
hard validation at push time, without ever silently discarding a
human's unstaged work and without ever crossing into semantic
(AI-driven) content generation in this version.

### Scope boundary this record fixes
`doc_sync.py` is client-agnostic in the same sense as `skills/spec` and
`skills/verify`: it knows the `SPEC.md` / `CHECKPOINT.md` convention
those two skills already establish, and it shells out to a project's
own `scripts/verify.py` for structural truth. It contains zero
domain-specific logic for any consuming project (no project name,
component name, or business concept appears in this script). This is
what keeps its addition to ToolTempest consistent with the client
independence commitment in ADR-0028 (in `mikkiola/article-pipeline`):
that record's "client" is the AI coding tool (Claude Code, Cursor,
Codex, Windsurf — enforced by refusing `.claude/`, `.cursor/`, etc. in
this repository), not the domain of the consuming project. `doc_sync.py`
never becomes a second source of truth for any consuming project's
architecture; it only keeps that project's own doc-owned files
structurally honest.

## Options
A — leave doc/code drift entirely to manual discipline (status quo
before this record). B — a fully autonomous AI-driven reconciliation
that rewrites doc content semantically inside the git hook (would
require a model call, an API key, network access, and non-determinism
on every commit). C — a structural-only, deterministic reconciliation
layer with a hard safety net (snapshot-before-modify, restore-on-fail,
hard-block on staged conflict), no AI call, chosen.

## Chosen
C.

## Why
Option A is the status quo this protocol replaces: nothing enforces
that `SPEC.md` / `CHECKPOINT.md` stay structurally well-formed as a
project evolves, and `scripts/verify.py` (in the consuming project)
can only report drift after the fact, never prevent it. Option B was
explicitly rejected during design: no signal source currently exists
in any consuming project's code to tell an AI reconciler what "done"
means for a given milestone, so any AI-authored edit to `status:` or
milestone completion would be invented, not derived — an unacceptable
risk to run unattended inside a commit-blocking hook. Option C fixes
only what is mechanically, deterministically knowable: whether a
`CHECKPOINT.md` block is missing one of its three required fields
(`verify:`, `done-when:`, `status:`). It never invents field *values*,
only adds a `TODO` placeholder for a missing field, leaving the actual
judgment call to the human. This keeps the "Zero-Human Intervention"
property scoped to *execution* of a narrow, safe, structural fix — not
to judgment about project state.

## Constraints

### pre-commit lifecycle (DETECT → RECONCILE → VALIDATE → RECORD → STAGE)
This order is invariant. `doc_sync.py pre-commit` refuses to reorder
these steps; a caller that needs a different order must stop and get
explicit human confirmation before doing so — this is a task-discipline
rule, not just an implementation default.

1. **DETECT** — runs `scripts/verify.py` (in the consuming project) once
   to discover every doc-owned file (`SPEC.md` with a paired
   `CHECKPOINT.md`, or `SPEC.md` with an inline `## Milestones`
   checklist) and its current structural status. Also reads
   `git diff --cached --name-only` to know which doc-owned files are
   already staged by the human.
2. **RECONCILE** — for each `CHECKPOINT.md` block reported MALFORMED
   because it is missing `verify:`, `done-when:`, or `status:`: if that
   file is already staged, `doc_sync.py` re-checks the *staged* blob
   (`git show :<path>`) against the same structural rule. If the staged
   version is already well-formed, nothing is done (the human already
   fixed it). If the staged version is still malformed, this is a
   conflict — **HARD BLOCK, exit 1**, before any file is touched,
   listing every conflicting path. Otherwise (file not staged), the
   missing field(s) are appended with a `TODO` placeholder value to the
   working-tree copy, after first taking a snapshot (see
   SNAPSHOT-BEFORE-MODIFY below). Inline `## Milestones` checkbox lines
   with an empty description are reported by `scripts/verify.py` as
   MALFORMED but are never auto-fixed — inventing description text is
   exactly the semantic judgment call this record excludes. They are
   left for a human and do not block the commit by themselves.
3. **VALIDATE** — re-runs `scripts/verify.py` against the now-reconciled
   working tree. Exit code 0 is required to proceed. On FAIL: every
   file `doc_sync.py` modified in RECONCILE this run is restored to its
   pre-RECONCILE snapshot (deleted if it did not exist before), and the
   hook exits 1. Nothing modified by RECONCILE survives a VALIDATE
   failure.
4. **RECORD** — only on VALIDATE success: writes one audit record to
   `.tempest/runs/docops_<run_id>.json`, matching
   `schemas/execution-record.schema.json`. `run_id` is a UTC timestamp
   plus a short random suffix (collision-safe within the same second);
   it is not, and never becomes, the commit SHA — the commit does not
   exist yet at this point in the hook's execution, so nothing in the
   record can reference it (see "commit_sha exclusion" below).
5. **STAGE** — only after RECORD: `git add` on every file RECONCILE
   modified plus the new `.tempest/runs/docops_<run_id>.json`. This is
   the only point in the entire pre-commit run where `git add` is
   invoked, and it happens strictly after VALIDATE has already
   succeeded — never before.

If DETECT finds no doc-owned files at all, or none are malformed, the
hook is a no-op that exits 0 immediately after DETECT — RECONCILE,
VALIDATE, RECORD, and STAGE do not run.

### SNAPSHOT-BEFORE-MODIFY (a safety mechanism inside RECONCILE, not a stage of its own)
Before `doc_sync.py` writes to a doc-owned file for the first time in a
given run, it captures that file's exact pre-modification bytes — in
process memory for the lifetime of the hook invocation, never written
to git in any form (no stash, no commit, no ref). If the file did not
exist before RECONCILE touched it, the snapshot records "did not
exist," not empty content.

On a VALIDATE failure, restoration replays exactly this snapshot: an
existing file's original bytes are rewritten as-is; a file that did not
exist is deleted. This is deliberately **not** `git checkout --
<path>`, which restores the last *committed* version and would destroy
any unstaged edits a human had made to that file before the hook ever
ran. The distinction matters precisely because a human may have
unstaged, uncommitted work in a doc-owned file at the moment they run
`git commit` on unrelated files — that work must survive a DocOps
failure exactly as it existed before DocOps touched anything.

### commit_sha exclusion from the audit record
The execution record has no `commit_sha` field, by design, not by
oversight: `pre-commit` runs strictly before Git computes the new
commit's SHA (which depends on the final tree, parent, message, and
timestamp — none of which are fixed yet when RECONCILE/VALIDATE/RECORD
execute). A record schema field that could never be populated correctly
at write time is worse than no field. Discovering which commit a given
audit record belongs to is instead a Git Tree lookup performed after
the fact (see "Audit lookup" below) — the record is retrievable
*because* it was staged and sealed into the same commit as the code and
doc changes it describes, not because it names that commit itself.

For the same reason, this version deliberately does **not** write any
"last-synced commit" or similar marker into doc-owned file content
during RECONCILE — the only commit identity available at RECONCILE time
is the *parent* HEAD, and encoding the (unknowable) upcoming child
commit into file content would be either wrong or misleading. RECONCILE
in this version is scoped strictly to the two field-completion and
inline-Milestones-checkbox findings `scripts/verify.py` already reports
structurally; a "last-synced" marker is out of scope for V2 and left
for a future record if this gap turns out to matter in practice.

### One Commit SHA Lineage
Because STAGE (`git add`) runs inside the same `pre-commit` hook
invocation that ultimately allows `git commit` to proceed, the code
changes the human staged, the doc-owned files RECONCILE touched, and
`.tempest/runs/docops_<run_id>.json` are always committed together, as
one atomic tree, under one commit SHA. There is no window in which one
of the three could be committed without the other two.

### pre-push lifecycle (hard validation only)
`doc_sync.py pre-push` performs exactly one check: it runs
`scripts/verify.py` and requires exit code 0. It is invoked alongside —
not instead of — the ADR-citation grep check and `gitleaks` already
present in the consuming project's `pre-push` hook. None of the three
checks modifies the working tree, stages anything, writes a git note,
or makes a network call. `git push` itself remains the standard,
unmodified Git command — DocOps adds no wrapper around it.

### Audit lookup and isolation
An audit record's relationship to the commit that contains it is read
natively from the Git object graph — for example
`git show <SHA> -- .tempest/runs/` — never from a field inside the JSON
itself. `.tempest/runs/` accumulates one file per successful DocOps run
across the project's history; nothing in this record prunes or rewrites
old run files (Immutable Lineage, per `mikkiola/article-pipeline`
ADR-0011, applies here by the same reasoning: an audit record tied to a
commit that no longer matches its original form is not a reliable
record of what was actually reconciled at that commit).

### Task Discipline: stage-order invariant
`git add` for DocOps-managed paths never runs before VALIDATE has
already succeeded in the same invocation. Any change to this ordering
requires stopping and asking a human first; it is not a decision an
automated agent implementing or modifying this protocol may make
unilaterally.

### Language
Every artifact this record introduces — this ADR, the JSON Schema,
`doc_sync.py` (code, docstrings, and all runtime output: log lines,
WARNING/FAIL messages, `timeline_summary` text), `skills/doc-sync/SKILL.md`,
and the `rules/drift-control.md` update — is English only, without
exception.

## Rejected
B — an AI-driven reconciler was rejected for this version specifically
because no consuming project currently exposes a "done" signal an AI
could reconcile against without inventing one; revisiting this is
possible in a future record once such a signal exists, but is out of
scope here. Silent auto-update of the *protocol itself* (as opposed to
its per-commit execution) was also rejected, consistent with
`mikkiola/article-pipeline` ADR-0026/0027/0028: this record's Boundary
of Autonomy applies "Zero-Human Intervention" only to a single
pre-commit/pre-push run, never to adopting a new ToolTempest version,
which remains the existing manual `.tooltempest.lock` +
`scripts/sync-tooling.sh` path.

## Consequences
`scripts/doc_sync.py`, `schemas/execution-record.schema.json`, and
`skills/doc-sync/SKILL.md` become new, permanent parts of ToolTempest's
canonical core, alongside the original three V1 primitives —
`scripts/doc_sync.py` is the first *executable* artifact in ToolTempest;
everything before it was markdown a client interprets on its own terms.
Consuming this repository from this record forward means, for any
project that opts in, pinning and executing code via local git hooks —
not only copying instructional text into a client's configuration
directory. A consuming project's own `.git/hooks/pre-commit` and
`.git/hooks/pre-push` must carry a header comment citing this record
(`Canon: ToolTempest ADR-0001`) plus the pinned `.tooltempest.lock` SHA,
so the source of the behavior is discoverable directly from the hook
file, without a separate reference file being required in the
consumer's own `docs/adr/`.

## Validation
Not yet validated end to end in a consuming project as of this record's
commit — `mikkiola/article-pipeline`'s integration (updating
`.tooltempest.lock`, running `scripts/sync-tooling.sh`, and building —
but not yet activating — the two hooks) is tracked separately in that
project's own history, not in this repository.

## Reversal condition
Revisit Option C (structural-only, no AI) if a consuming project
develops a reliable, derivable "done" signal (for example, a CI status
check, a test-coverage gate, or an explicit machine-readable milestone
marker updated by something other than a human's judgment) — at that
point, a bounded, explicitly-scoped AI-assisted RECONCILE mode could be
proposed as a new record, not as a silent extension of this one. Until
such a record exists, RECONCILE stays structural-only.

## Source
`mikkiola/article-pipeline` owner request, specifying all ten
invariants fixed above; the RECONCILE-signal ambiguity (option B vs. C)
and the ToolTempest V1→V2 scope-boundary question were both raised back
to the owner rather than resolved by assumption, per that project's
own standing "ask on unresolved design forks" rule.
