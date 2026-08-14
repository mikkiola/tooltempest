# ToolTempest

Client-agnostic canonical source for a small set of shared tooling
primitives: `/spec`, `/verify`, and `drift-control`. This repository
holds only the primitives themselves — no client-specific integration
code lives here.

## What this is

ToolTempest exists to give these primitives one canonical home,
independent of any particular AI coding tool or client. It was split
out of the Article Pipeline project, where the same three files were
previously kept as local, client-specific configuration.

## Composition (V1)

Exactly three files, nothing else:

- `skills/spec/SKILL.md`
- `skills/verify/SKILL.md`
- `rules/drift-control.md`

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
ToolTempest in a consuming project is a manual process:

1. Clone this repository and check out the specific commit SHA you
   intend to use (do not float on a branch).
2. Copy the three files into place for your client, e.g. for Claude
   Code:
   - `skills/spec/SKILL.md` → `~/.claude/skills/spec/SKILL.md`
   - `skills/verify/SKILL.md` → `~/.claude/skills/verify/SKILL.md`
   - `rules/drift-control.md` → `~/.claude/rules/drift-control.md`
3. Record the full commit SHA you used in your consuming project's
   `.tooltempest.lock` file, so the exact version in use is
   reproducible later.

## Scope

This repository is the mechanism only: the three primitive files
described above. Project-specific content (for example, a given
project's own `CHECKPOINT.md`) does not belong here and is not
included.
