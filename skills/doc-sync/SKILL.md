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
   `done-when:`, or `status:`: if that file is not already staged, the
   missing field is added with a literal `TODO` placeholder value. If
   the file *is* already staged and the staged content is still
   malformed, the whole commit is blocked (exit 1) before anything is
   touched — DocOps never silently overwrites a human's in-progress
   staged edit. RECONCILE never invents a field's actual value, and
   never touches an inline `## Milestones` checkbox with an empty
   description (no safe auto-fix exists for that).
3. **VALIDATE** — re-runs `scripts/verify.py` against the reconciled
   tree. If it still fails, every file RECONCILE touched this run is
   restored to its exact pre-RECONCILE content (or deleted, if it did
   not exist before) — never via `git checkout --`, which would instead
   discard any unstaged human edits that predate this run. The commit
   is blocked (exit 1).
4. **RECORD** — only reached after VALIDATE passes: writes one audit
   record to `.tempest/runs/docops_<run_id>.json`
   (`schemas/execution-record.schema.json` in this repository).
5. **STAGE** — `git add`s every file RECONCILE modified, plus the new
   record file. This is the only `git add` DocOps ever runs, and it
   never runs before VALIDATE has already succeeded.

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
| `counters.updated` | of those, how many RECONCILE actually auto-fixed |
| `token_usage` | always zero in this protocol version — reserved for a possible future AI-assisted RECONCILE mode (see ADR-0001, Reversal condition); no model call happens today |
| `result` | always `"SUCCESS"` — a FAIL run never reaches RECORD, so it never produces a file to read |

## If a commit was blocked

- **"staged doc-owned file(s) are already structurally malformed"** —
  RECONCILE found a conflict between what you staged and what
  `scripts/verify.py` requires. Either fix the staged content by hand,
  or run `git restore --staged <path>` and let RECONCILE fix it
  automatically on the next commit attempt.
- **"scripts/verify.py did not pass after RECONCILE"** — something
  `scripts/verify.py` flags cannot be auto-fixed (for example, an empty
  Milestones checkbox description). RECONCILE has already reverted its
  own edits; fix the underlying issue by hand and retry.

## What this skill does not cover

Adopting a new ToolTempest version (a new `doc_sync.py`, a protocol
change, a new field in the schema) is never automatic — see
`.tooltempest.lock` and `scripts/sync-tooling.sh` in the consuming
project, and ADR-0026/0027/0028 (`mikkiola/article-pipeline`) for that
separate, human-triggered lifecycle. This skill is about what a single
commit/push does under an *already-installed* pinned version, not about
installing or updating that version.
