# ToolTempest

Client-agnostic canonical source for a small set of shared tooling
primitives: `/spec`, `/verify`, `drift-control`, and (as of V2) the
DocOps Protocol (`doc_sync`). This repository holds only the primitives
themselves — no client-specific integration code lives here.

## What this is

ToolTempest exists to give these primitives one canonical home,
independent of any particular AI coding tool or client. It was split
out of the Article Pipeline project, where the same three files were
previously kept as local, client-specific configuration.

## Composition (V2)

Seven files, across two kinds of content:

Markdown primitives (V1, unchanged):
- `skills/spec/SKILL.md`
- `skills/verify/SKILL.md`
- `rules/drift-control.md`

DocOps Protocol (V2, added by ADR-0001 — `docs/adr/0001-docops-protocol.md`):
- `scripts/doc_sync.py`
- `schemas/execution-record.schema.json`
- `skills/doc-sync/SKILL.md`

**This is a real change in kind, not just in count.** V1 was exclusively
markdown — passive instructional text a client interprets on its own
terms, by reading it. `scripts/doc_sync.py` is the first executable
code in ToolTempest: a Python script meant to be invoked directly from
a consuming project's git hooks, carrying its own runtime logic
(structural reconciliation, snapshot-before-modify, exit codes, a JSON
Schema for its own audit record). Composition (V2) now includes
executable tooling, not only markdown-based primitives. Consuming this
repository now means, for a project that opts in, executing pinned code
via git hooks — not only copying instructional text into a client's
config directory. See `docs/adr/0001-docops-protocol.md` for the full
protocol contract and the scope-boundary reasoning for why this addition
does not compromise client independence (below).

## Version identity

The canonical identity of any version of this content is the full
40-character Git commit SHA that introduced it — not a tag. Tags, if
used, are human-readable aliases only; they are not the source of
truth. When pinning or recovering a specific version, always record
and match on the full commit SHA.

## Client independence

Staying client-agnostic is an architectural commitment for this
repository: it does not and will not contain `.claude/`, `.cursor/`,
or any other client-specific directory. Isolating the differences
between consuming tools (Claude Code, and potentially others such as
Cursor, Codex, or Windsurf in the future) is expected to be the job of
a separate CLI adapter layer. That adapter has not been built yet —
its design is a separate, future task and is out of scope for this
repository as it stands today.

## Update model

The intended lifecycle separates two concerns:

- **Discovery** — noticing that a newer version exists — is expected
  to happen automatically.
- **Install / activation** — actually adopting a new version in a
  consuming project — is expected to always require an explicit
  action. Silent auto-update is not an acceptable behavior under this
  contract.

This is an architectural commitment for how consumers of ToolTempest
should behave. No discovery or update mechanism is implemented in
this repository itself.

## Offline recovery

If a consumer has a specific commit SHA cached locally, that cached
copy remains usable without network access. This is *not* the same as
general offline reproducibility — recovering a version you have not
previously cached still requires reaching GitHub.

## Usage today (manual — no adapter exists yet)

There is currently no CLI or adapter tooling. Until one exists, using
ToolTempest in a consuming project is a manual process, and differs by
which part of the composition a project adopts.

**V1 markdown primitives** — copied into place for your client, e.g.
for Claude Code:

1. Clone this repository and check out the specific commit SHA you
   intend to use (do not float on a branch).
2. Copy the three files:
   - `skills/spec/SKILL.md` → `~/.claude/skills/spec/SKILL.md`
   - `skills/verify/SKILL.md` → `~/.claude/skills/verify/SKILL.md`
   - `rules/drift-control.md` → `~/.claude/rules/drift-control.md`
3. Record the full commit SHA you used in your consuming project's
   `.tooltempest.lock` file, so the exact version in use is
   reproducible later.

**V2 DocOps Protocol** — opt-in, per project, wired through local git
hooks rather than copied into a client config directory:

1. Same clone-and-pin step as above; `scripts/doc_sync.py` is run from
   wherever the consuming project's own tooling makes it available
   (for example, `scripts/sync-tooling.sh` in `mikkiola/article-pipeline`
   copies it in alongside the V1 primitives).
2. The consuming project's `.git/hooks/pre-commit` and
   `.git/hooks/pre-push` call `doc_sync.py pre-commit` /
   `doc_sync.py pre-push` respectively, and carry a header comment
   citing `ADR-0001` plus the pinned `.tooltempest.lock` SHA — see
   `docs/adr/0001-docops-protocol.md` for the full contract.
3. `schemas/execution-record.schema.json` and `skills/doc-sync/SKILL.md`
   are reference material for the resulting `.tempest/runs/` audit
   records; neither needs to be copied anywhere to be useful — read
   them directly from the pinned checkout or this repository.

## Connecting a new project

A step-by-step checklist for wiring up a brand-new ToolTempest
consumer. This restates "Usage today" above as a linear sequence; for
*why* each step exists, see ADR-0001
(`docs/adr/0001-docops-protocol.md`) and ADR-0002
(`docs/adr/0002-tier2-doc-sync.md`) in this repository.

1. **Pin `.tooltempest.lock`.** Create (or update) the consuming
   project's `.tooltempest.lock` with the full 40-character commit SHA
   you intend to use — never a branch name or tag. See "Version
   identity" above.
2. **Initial install.** ToolTempest itself ships no installer script —
   a consuming project needs its own, following the pattern already
   proven in `mikkiola/article-pipeline`'s `scripts/sync-tooling.sh`:
   clone this repository, check out the pinned SHA, and copy the V1
   primitives plus `scripts/doc_sync.py` /
   `schemas/execution-record.schema.json` into place, per "Usage
   today" above.
3. **Activate the git hooks.** Again following article-pipeline's
   pattern (`scripts/install-hooks.sh`, also not part of this
   repository): install `pre-commit` and `pre-push` hooks that call
   `doc_sync.py pre-commit` / `doc_sync.py pre-push`, with a header
   comment citing `ADR-0001` and the pinned SHA, per "Usage today"
   above. Re-run this after every resync so `.git/hooks/` picks up any
   hook-source changes.
4. **Optional but recommended: Drift Warning.** ToolTempest does not
   provide this — it is not part of Composition (V2) and does not come
   for free just by adopting `doc_sync.py`. It lives entirely in
   `mikkiola/article-pipeline`'s own `scripts/hooks/pre-push`, not in
   this repository, so each consumer that wants it has to replicate
   the pattern into its own pre-push hook independently. See
   `mikkiola/article-pipeline`'s `docs/adr/0032-drift-warning.md` for
   the design and that repository's `scripts/hooks/pre-push` for the
   reference implementation (a `git ls-remote` check against this
   repository's `origin/main`, compared against the consumer's own
   `.tooltempest.lock` pin).
5. **Reading a HARD BLOCK or WARNING.** Both come from `doc_sync.py`
   or the consumer's own `scripts/verify.py`, printed directly to the
   terminal at commit/push time — read that output first, not "ask
   whoever set this up":
   - A **HARD BLOCK** (`doc_sync.py pre-commit` or `pre-push` exits
     non-zero) means either a staged file is genuinely structurally
     invalid, or `scripts/verify.py` itself failed to run — the
     printed message states which, and what to do about each case.
     See ADR-0001 for the underlying RECONCILE/VALIDATE contract.
   - A **WARNING** (Drift Warning, if replicated per step 4 above, or
     any similar consumer-side check) never blocks anything — it's
     informational. Read the printed line for which command to run
     next (e.g. `scripts/sync-tooling.sh`).

## Scope

This repository is the mechanism only: the seven files described in
"Composition (V2)" above. Project-specific content (for example, a
given project's own `CHECKPOINT.md`, or its own `scripts/verify.py`)
does not belong here and is not included. `doc_sync.py` calls a
consuming project's `scripts/verify.py` as a subprocess but contains no
knowledge of any specific project's domain, components, or content.
