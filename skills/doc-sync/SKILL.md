---
name: doc-sync
description: "Explains how DocOps checks SPEC.md at commit and push time: the shared checker scripts/docops.py (ADR-0013, ADR-0014). Triggers on: docops, doc sync, docops.py, why did my commit block on SPEC.md, OUT_OF_SCOPE_SPEC. NOT for: writing a new SPEC.md (use /spec), running typecheck/lint/test/build (use /verify)."
user-invocable: true
disable-model-invocation: false
allowed-tools: Read, Bash, Grep
---

# DocOps

DocOps checks the SPEC files of a consuming project from that project's own
`.git/hooks` or hook directory. The mechanism this repository ships is the
shared checker, `scripts/docops.py` (ADR-0013, ADR-0014). Look at the
project's own hook files to see what it actually runs; do not assume. This
skill is reference material for understanding and troubleshooting what a hook
did; it does not itself modify anything.

The earlier `scripts/doc_sync.py` hooks are retired and are no longer shipped
(ADR-0001, ADR-0010 record them). A consumer whose hook files still call
`doc_sync.py` runs a copy installed from an earlier pin; this skill does not
describe it.

## The shared checker

A project that has migrated wires its own hooks to the checker; the wiring
belongs to that project, and this repository defines only the checker's command
line. A hook typically runs `python3 scripts/docops.py check --tree index` at
commit time and `python3 scripts/docops.py check --push-stdin` at push time; read the
project's hook files for the actual commands. `check` prints one JSON
result; `report` takes the same arguments and prints the same result as text,
which is easier to read when a hook blocks. The checker reads an exact Git
snapshot (the index, a commit, or each pushed commit) and never the working
tree. It writes nothing: no files, no staging, no commit, no network. In
particular it writes no run record: it produces no `.tempest/runs/` record,
which belonged to the retired hooks.

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

## What this skill covers, and what it doesn't

Adopting a new ToolTempest version (a new checker, a protocol change, a new
field in a schema) is never automatic: a consumer pins a commit in
`.tooltempest.lock` and installs the pinned files itself. This skill is about
what a hook does under an *already-installed* pinned version, not about
installing or updating that version, and not about installing this skill into a
client's skill directory (that belongs to a client adapter that does not exist
yet).
