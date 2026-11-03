---
name: doc-sync
description: "Explains how DocOps checks SPEC.md at commit and push time: the shared checker scripts/docops.py (ADR-0013, ADR-0014) and, for a consumer whose hooks have not migrated, the legacy scripts/doc_sync.py hooks and their audit record (ADR-0001, ADR-0010). Triggers on: docops, doc sync, docops.py, doc_sync.py, why did my commit block on SPEC.md, OUT_OF_SCOPE_SPEC, DocOps audit record. NOT for: writing a new SPEC.md (use /spec), running typecheck/lint/test/build (use /verify)."
user-invocable: true
disable-model-invocation: false
allowed-tools: Read, Bash, Grep
---

# DocOps

DocOps checks the SPEC files of a consuming project from that project's own
`.git/hooks` or hook directory. There are two mechanisms. Look at the
project's own hook files to see which one it runs; do not assume. This skill
is reference material for understanding and troubleshooting what a hook did;
it does not itself modify anything.

- **The shared checker, `scripts/docops.py`** (ADR-0013, ADR-0014): the
  current mechanism. Read-only; writes no run record.
- **The legacy hooks, `scripts/doc_sync.py` with the project's own
  `scripts/verify.py`** (ADR-0001, ADR-0010): still shipped in this
  repository for a consumer that has not migrated. A consumer is on this path
  only if its own hook files still call `doc_sync.py`.

## The shared checker

A project that has migrated wires its own hooks to the checker; the wiring
belongs to that project, and this repository defines only the checker's command
line. A hook typically runs `python3 scripts/docops.py check --tree index` at
commit time and `python3 scripts/docops.py check --push-stdin` at push time; read the
project's hook files for the actual commands. `check` prints one JSON
result; `report` takes the same arguments and prints the same result as text,
which is easier to read when a hook blocks. The checker reads an exact Git
snapshot (the index, a commit, or each pushed commit) and never the working
tree. It writes nothing: no files, no staging, no commit, no network, and no
run record. The `.tempest/runs/` audit record of the legacy path is not
produced by the checker.

### Which SPEC files it evaluates

- The root `SPEC.md` is always evaluated, exactly once, with or without a
  `.docops-policy.json`. If the root `SPEC.md` is absent and no policy entry
  names it, there is no SPEC and the check is N/A (not a failure). A policy
  entry that names the root while the file is absent is a FAIL
  (`SPEC_MISSING`), as for any registered path.
- An optional `.docops-policy.json` at the repository root adds files:
  `specs` registers further active tracks (`{path, track}`), and
  `non_active_specs` lists, by exact file path and with a reason, `SPEC.md`
  files that are not project specifications (a test fixture, historical
  material). An explicit entry for the root `SPEC.md` is allowed and is still
  evaluated only once. The policy is read from the snapshot being checked.
- The checker inventories every tracked file named `SPEC.md`, at any depth and
  in any letter case. Each must be the root SPEC, a registered active track or
  a listed non-active file. **Any other `SPEC.md` is a FAIL (exit 1)**, code
  `OUT_OF_SCOPE_SPEC`, and the message names the path. It is never skipped and
  never a warning. Fix it by registering it in `specs`, listing it in
  `non_active_specs` with a reason, or removing it. Names such as `SPEC.txt`
  are not detected.
- Also a FAIL: a path in two categories, a duplicate `non_active_specs` entry,
  the root listed as non-active, a non-active entry without a reason, a
  non-active entry for a file that no longer exists, a registered path that is
  a directory (`SPEC_IS_DIRECTORY`), and a track named `root` at another path
  beside a root `SPEC.md` when the policy has no explicit root entry
  (`SPEC_CLASSIFICATION_CONFLICT`). An invalid
  `.docops-policy.json` (unreadable, wrong shape, duplicate `specs` path) is a
  tool failure (exit 2), never a pass.

### What a SPEC must look like

A SPEC with `spec-state: idle` in frontmatter (`---` / `spec-state: idle` /
`---` at the very top) declares no active task and is N/A. Otherwise the SPEC
is active and needs exactly one `## Milestones` section holding flat
`- [ ]` / `- [x]` checkbox items with text, and none of the constructs the
grammar denies (HTML, blockquotes, nested lists, lines indented four columns
or more, headings with markup in levels 1 and 2, and others). The checker
names the construct and the line. See ADR-0013 for the full grammar and
`/spec` for the template that passes it.

### Exit codes

A hook should treat any non-zero exit as a block.

| Exit | Meaning |
|---|---|
| 0 | every evaluated check is PASS or N/A |
| 1 | a violation (FAIL), or a supplied impact report's required assessment is not complete |
| 2 | no verdict: usage, dependency, I/O, Git, policy or internal error. Never a pass |
| 3 | SPEC applicability cannot be established. Defined, but no SPEC scope rule produces it any more |

1 takes precedence over 3. The result separates the checked dimension
(`SPEC_STRUCTURE`) from those it does not assess (`assessed: false`): a PASS
says only that the SPEC files are structurally valid and classified, nothing
about tests, review, documentation impact or acceptance.

### If a commit or push was blocked

Run `python3 scripts/docops.py report --tree index` (or `--tree HEAD`) and read
the finding lines.

- **`OUT_OF_SCOPE_SPEC`** — a `SPEC.md` that is not classified; see above.
- **`SPEC_MISSING`** — a registered path has no file in the snapshot.
- **`MILESTONES_MISSING`** — the SPEC has no `## Milestones` section (no line
  number). A prose-only "no active task" needs the `spec-state: idle`
  frontmatter instead.
- **`CHECKBOX_*`, `DENIED_*`, `FRONTMATTER_*`** — the SPEC breaks the grammar;
  the line number is given.
- **`POLICY_INVALID` / exit 2** — `.docops-policy.json` cannot be used.
- **A missing script** — if `scripts/docops.py` is not installed in this clone,
  the interpreter reports the missing file (typically exit 2); that is a setup
  problem, not a SPEC problem.

## The legacy path (consumers whose hooks still call `doc_sync.py`)

`doc_sync.py` validates only the `inline_spec`/`## Milestones` pattern
(ADR-0010, `docs/adr/0010-retire-checkpoint-md-support.md`) — a
`CHECKPOINT.md`-based pattern was supported through V2 but is now fully
removed, not merely deprecated. A `CHECKPOINT.md` file sitting in a repo
today is inert as far as this protocol is concerned.

Everything below applies only to a project whose own hooks still run
`scripts/doc_sync.py`. For a project on the shared checker, none of it
happens: no DETECT/VALIDATE/RECORD/STAGE steps, no `.tempest/runs/` record.

`pre-commit` always starts with DETECT and then takes one of five paths.
The four named steps (DETECT, VALIDATE, RECORD, STAGE) run in that order,
but the last three run **only in one narrow case**: an ordinary commit does
not reach them and leaves no audit record.

1. **DETECT** — runs the consuming project's own `scripts/verify.py` to
   discover every inline-Milestones `SPEC.md` and its current
   structural status. What happens next depends on what it returned:
   - **No `SPEC.md` found** (`scripts/verify.py` exits 2): the hook prints
     "no SPEC.md files found" and exits 0. Nothing else runs.
   - **`scripts/verify.py` itself is unusable** (missing, a syntax error,
     a crash, unreadable output): the commit is blocked (exit 1).
   - **A staged component has no `## Milestones` checklist** (pattern
     `UNKNOWN`): the commit is blocked (exit 1). An `UNKNOWN` component
     the commit does not touch does not block.
   - **Every doc-owned file is structurally OK** (the ordinary case): the
     hook prints "all structurally OK" and exits 0. VALIDATE, RECORD and
     STAGE do not run; nothing is written and nothing is staged.
   - **At least one doc-owned file is MALFORMED:** go on to VALIDATE.
2. **VALIDATE** — only in that last case: re-runs `scripts/verify.py`
   against the same tree DETECT just scanned (doc_sync.py never writes to
   any doc-owned file, so nothing it wrote can have changed the result).
   If it still fails — typically an inline `## Milestones` checkbox
   with an empty description, which is never auto-fixed — the commit
   is blocked (exit 1, "scripts/verify.py still failed"; if the re-run
   crashes instead, "did not produce readable output"). This applies to
   any MALFORMED doc-owned file, whether or not the commit touches it:
   unlike the `UNKNOWN` case, the MALFORMED check is not limited to
   staged components. Only if the re-run passes, which means the tree
   changed or `scripts/verify.py` returned a different result for the
   same tree, does the hook continue.
3. **RECORD** — only reached after VALIDATE passes: writes one audit
   record to `.tempest/runs/docops_<run_id>.json`
   (`schemas/execution-record.schema.json` in this repository).
4. **STAGE** — `git add`s the new record file (plus any pruned old run
   records). This is the only `git add` DocOps ever runs, and it never
   runs before VALIDATE has already succeeded.

So a record exists only for a commit where DETECT saw a MALFORMED file and
the VALIDATE re-run passed. A commit that succeeds because every file was
OK has no record, and a blocked commit never has one.

`pre-push` runs exactly one DocOps check: `scripts/verify.py`, hard-fail
on non-zero exit. It never modifies the working tree, stages anything,
writes a git note, or makes a network call — it runs alongside (not
instead of) the ADR-citation and `gitleaks` checks already in a
project's `pre-push` hook.

### Reading a legacy audit record

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

### If a legacy commit was blocked

- **"staged component(s) have an unrecognized doc structure (UNKNOWN
  pattern)"** — the component's `SPEC.md` has no `## Milestones`
  checklist at all. Add one with checkbox lines before committing
  changes to it.
- **"scripts/verify.py still failed"** — something `scripts/verify.py`
  flags cannot be auto-fixed (for example, an empty `## Milestones`
  checkbox description), in any doc-owned file, touched by the commit or
  not. Nothing was touched, so there is nothing to revert; fix the
  underlying issue by hand and retry.
- **"scripts/verify.py did not produce readable output"** —
  `scripts/verify.py` itself is missing, has a syntax error, crashed or
  printed something unreadable (on DETECT or on the VALIDATE re-run).
  This is usually a bug in that script, not in your commit.

## What this skill covers, and what it doesn't

Adopting a new ToolTempest version (a new checker, a protocol change, a new
field in a schema) is never automatic: a consumer pins a commit in
`.tooltempest.lock` and installs the pinned files itself. This skill is about
what a hook does under an *already-installed* pinned version, not about
installing or updating that version, and not about installing this skill into a
client's skill directory (that belongs to a client adapter that does not exist
yet).
