---
name: doc-sync
description: "Explains the DocOps Protocol (ADR-0001, ADR-0010): what scripts/doc_sync.py does when it runs as a git pre-commit/pre-push hook, and how to read a .tempest/runs/docops_<run_id>.json audit record. Triggers on: docops, doc sync, doc_sync.py, why did my commit block on SPEC.md, DocOps audit record. NOT for: writing a new SPEC.md (use /spec), running typecheck/lint/test/build (use /verify)."
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

`pre-commit` runs four steps, strictly in this order, every time:

1. **DETECT** — runs the consuming project's own `scripts/verify.py` to
   discover every inline-Milestones `SPEC.md` and its current
   structural status.
2. **VALIDATE** — re-runs `scripts/verify.py` against the same tree
   DETECT just scanned (doc_sync.py never writes to any doc-owned
   file, so there is nothing for this second run to have changed). If
   anything is still MALFORMED here — typically an inline
   `## Milestones` checkbox with an empty description, which is never
   auto-fixed — the commit is blocked (exit 1).
3. **RECORD** — only reached after VALIDATE passes: writes one audit
   record to `.tempest/runs/docops_<run_id>.json`
   (`schemas/execution-record.schema.json` in this repository).
4. **STAGE** — `git add`s the new record file (plus any pruned old run
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
proceed, the code the human staged and the audit record itself always
land in the same commit —
One Commit SHA Lineage, per ADR-0001.

Fields worth knowing when reading a record:

| Field | Meaning |
|---|---|
| `counters.scanned` | doc-owned files DETECT found this run |
| `counters.affected` | of those, how many were MALFORMED |
| `counters.updated` | always `0` — doc_sync.py never writes to a doc-owned file, so nothing is ever "updated"; kept in the schema for compatibility |
| `token_usage` | always zero in this protocol version — no model call happens today |
| `result` | always `"SUCCESS"` — a FAIL run never reaches RECORD, so it never produces a file to read |

## If a commit was blocked

- **"staged component(s) have an unrecognized doc structure (UNKNOWN
  pattern)"** — the component's `SPEC.md` has no `## Milestones`
  checklist at all. Add one with checkbox lines before committing
  changes to it.
- **"scripts/verify.py still failed"** — something `scripts/verify.py`
  flags cannot be auto-fixed (for example, an empty `## Milestones`
  checkbox description). Nothing was touched, so there is nothing to
  revert; fix the underlying issue by hand and retry.

## What this skill covers, and what it doesn't

`doc_sync.py` validates only the `inline_spec`/`## Milestones` pattern
(ADR-0010, `docs/adr/0010-retire-checkpoint-md-support.md`) — a
`CHECKPOINT.md`-based pattern was supported through V2 but is now fully
removed, not merely deprecated, because no consuming project's own
`scripts/verify.py` has recognized it since each independently adopted
its own "CHECKPOINT.md pattern deprecated" decision. A `CHECKPOINT.md`
file sitting in a repo today is inert as far as this protocol is
concerned.

Adopting a new ToolTempest version (a new `doc_sync.py`, a protocol
change, a new field in the schema) is never automatic — see
`.tooltempest.lock` and `scripts/sync-tooling.sh` in the consuming
project, and ADR-0026/0027/0028 (`mikkiola/article-pipeline`) for that
separate, human-triggered lifecycle. This skill is about what a single
commit/push does under an *already-installed* pinned version, not about
installing or updating that version.
