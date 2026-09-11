---
name: doc-sync
description: "Explains the DocOps Protocol (ADR-0001): what scripts/doc_sync.py does when it runs as a git pre-commit/pre-push hook, and how to read a .tempest/runs/docops_<run_id>.json audit record. Triggers on: docops, doc sync, doc_sync.py, reconcile, why did my commit change SPEC.md/CHECKPOINT.md, DocOps audit record. NOT for: writing a new SPEC.md (use /spec), running typecheck/lint/test/build (use /verify)."
user-invocable: true
disable-model-invocation: false
allowed-tools: Read, Bash, Grep
---

# DocOps Protocol

`scripts/doc_sync.py` runs automatically from a consuming project's own
`.git/hooks/pre-commit` and `.git/hooks/pre-push`, once that project has
opted in per ADR-0001. This skill is reference material for
understanding and troubleshooting what it did on a given commit — it
does not itself modify anything.

## What actually happens on a commit

`pre-commit` runs five steps, strictly in this order, every time:

1. **DETECT** — runs the consuming project's own `scripts/verify.py` to
   discover every `SPEC.md`/`CHECKPOINT.md` pair and inline-Milestones
   `SPEC.md`, and their current structural status.
2. **RECONCILE** — for a `CHECKPOINT.md` block missing `verify:`,
   `done-when:`, or `status:`: DocOps writes nothing into the
   document — staged or not. The whole commit is blocked (exit 1)
   before anything is touched, and DocOps reports the gap instead of
   filling it, one pair per missing field:

   ```
   [DRIFT] CHECKPOINT.md: missing required field `verify`
   [QUESTION] What should `verify` be?
   ```

   RECONCILE never invents a field's actual value and never writes a
   placeholder for one — not even an honest `TODO` — because writing
   anything into the canonical document without a human decision
   authorizing that value is itself the violation (Hub Rules v3.6 Rule
   3: ask one specific question, don't fill the gap yourself — a
   placeholder still fills it, structurally). It never touches an
   inline `## Milestones` checkbox with an empty description either,
   for the same reason.
3. **VALIDATE** — re-runs `scripts/verify.py` against the tree,
   unchanged from what DETECT saw (RECONCILE never writes or reverts
   anything — see above). This step only runs once RECONCILE found no
   missing CHECKPOINT.md field; if `scripts/verify.py` still fails
   here — typically an inline `## Milestones` checkbox with an empty
   description, which is never auto-fixed — the commit is blocked
   (exit 1). There is nothing to restore, because nothing was touched.
4. **RECORD** — only reached after VALIDATE passes: writes one audit
   record to `.tempest/runs/docops_<run_id>.json`
   (`schemas/execution-record.schema.json` in this repository).
5. **STAGE** — `git add`s the new record file (plus any pruned old run
   records). This is the only `git add` DocOps ever runs, and it never
   runs before VALIDATE has already succeeded.

`pre-push` runs exactly one DocOps check: `scripts/verify.py`, hard-fail
on non-zero exit. It never modifies the working tree, stages anything,
writes a git note, or makes a network call — it runs alongside (not
instead of) the ADR-citation and `gitleaks` checks already in a
project's `pre-push` hook.

## Reading an audit record

An audit record has **no `commit_sha` field** — `pre-commit` runs
before Git computes the new commit's SHA, so nothing in the record
could name it correctly. The record's relationship to "its" commit is
instead a Git Tree fact, not a JSON field:

```bash
git show <SHA> -- .tempest/runs/
```

Because STAGE runs inside the same hook invocation that lets the commit
proceed, the code the human staged, whichever doc-owned files RECONCILE
touched, and the audit record itself always land in the same commit —
One Commit SHA Lineage, per ADR-0001.

Fields worth knowing when reading a record:

| Field | Meaning |
|---|---|
| `counters.scanned` | doc-owned files DETECT found this run |
| `counters.affected` | of those, how many were MALFORMED |
| `counters.updated` | always `0` — RECONCILE no longer writes or auto-fixes anything; a run that finds a missing field blocks the commit before RECORD is ever reached, so this field is always zero in any record that exists |
| `token_usage` | always zero in this protocol version — reserved for a possible future AI-assisted RECONCILE mode (see ADR-0001, Reversal condition); no model call happens today |
| `result` | always `"SUCCESS"` — a FAIL run never reaches RECORD, so it never produces a file to read |

## If a commit was blocked

- **"staged doc-owned file(s) are missing required field(s)"** —
  RECONCILE found a `CHECKPOINT.md` block missing one of its required
  fields, reported as a `[DRIFT]`/`[QUESTION]` pair per field. There is
  no auto-fix: RECONCILE writes nothing, so fix the file by hand with
  the answer to each question, then re-stage and commit again.
- **"scripts/verify.py still failed even though no CHECKPOINT.md field
  was missing"** — something `scripts/verify.py` flags cannot be
  auto-fixed (for example, an empty Milestones checkbox description).
  Nothing was touched, so there is nothing to revert; fix the
  underlying issue by hand and retry.

## What this skill does not cover

Adopting a new ToolTempest version (a new `doc_sync.py`, a protocol
change, a new field in the schema) is never automatic — see
`.tooltempest.lock` and `scripts/sync-tooling.sh` in the consuming
project, and ADR-0026/0027/0028 (`mikkiola/article-pipeline`) for that
separate, human-triggered lifecycle. This skill is about what a single
commit/push does under an *already-installed* pinned version, not about
installing or updating that version.
