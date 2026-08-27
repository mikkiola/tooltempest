# ADR-0008: Reference Documentation as a New Composition Category

## Status

Accepted

## Context & Constraints

README.md's "Scope" section states this repository "is the mechanism
only: the seven files described in 'Composition (V2)' above" — the
four Composition directories (`scripts/`, `schemas/`, `skills/`,
`rules/`), each git-tracked file in them treated as vendored to every
consumer via `MANIFEST.txt` and the consumer-side copy convention
(ADR-0006). `rules/` specifically carries a narrow, confirmed
semantic: `rules/drift-control.md` is a path-scoped Claude Code rule
file (YAML frontmatter `paths: ["**/CHECKPOINT.md", "**/progress.txt"]`)
copied verbatim to a consumer's `~/.claude/rules/`, not a general
governance-document category.

A new need arose: a shared reference document (the "Canonical
Documentation Bible" — structural and style rules for
`CONSTITUTION.md`/`ARCHITECTURE.md`/`ROADMAP.md`/`BACKLOG.md`/
`docs/adr/`, intended to apply uniformly across every project in this
ecosystem: `article-pipeline`, `tooltempest`, Radar, and future Brain)
does not fit any of the four existing Composition directories' actual
semantics. It is not a Claude Code client rule scoped to specific file
globs, not a skill, not a script, and not a schema. Placing it in
`rules/` anyway — which was the initial default considered — would
have silently overloaded that directory's meaning without recording
the change anywhere, and would have made the Bible eligible for the
same `~/.claude/rules/` copy-in-place convention as `drift-control.md`,
which does not fit content meant to be read and followed in place
across a whole document set, not activated per matching file path.

Constraint: this repository has exactly one real consumer today
(article-pipeline, per ADR-0007's own stated constraint) and no others
yet, though the Bible's own scope is explicitly ecosystem-wide, not
limited to that one consumer.

## Decision

ToolTempest's scope expands from "mechanism only" to "mechanism +
shared reference documentation." A new top-level directory,
`docs/reference/`, holds documents in this new category: content meant
to be read and followed by a consuming project's Claude Code sessions,
but **not** copied into a client config directory the way `rules/`
files are — no `~/.claude/` destination. A reference document is
consulted in place: a consuming project points to it, or vendors its
own copy into its own repository, rather than installing it as a rule.

First file in the category: `docs/reference/documentation-rules.md`.

`docs/reference/` joins the four existing Composition directories as a
fifth tracked directory in `MANIFEST.txt`'s ground truth. This is a
deliberate part of this Decision, not a default: every vendored file
should be listed in the manifest regardless of category, so
`scripts/check_manifest.py`'s `COMPOSITION_DIRS` tuple is updated from
`("scripts/", "schemas/", "skills/", "rules/")` to
`("scripts/", "schemas/", "skills/", "rules/", "docs/reference/")`, and
`docs/reference/documentation-rules.md` is added to
`MANIFEST.txt` in the same commit that adds the file itself.

## Alternatives & Rationale

**Place the Bible in `rules/` anyway** — rejected. `rules/` has a
specific, different semantic per `drift-control.md`'s own frontmatter
pattern (a path-scoped Claude Code rule, activated by matching a
specific file glob). The Bible has no natural single-file `paths:`
scope — it governs several documents' structure collectively — and
forcing it in would silently overload the directory's meaning with no
record of the change.

**Place the Bible in `mikkiola/article-pipeline` instead of
`tooltempest`** — rejected. The Bible applies to all ecosystem
projects equally, not specifically to article-pipeline as one
consumer among several. `tooltempest` is the actual shared home every
consumer already points to via `.tooltempest.lock`, making it the
correct canonical location for content meant to be shared, not owned
by one consumer.

**New `docs/reference/` category in `tooltempest`** — chosen. Matches
this repository's own stated purpose ("client-agnostic canonical
source for shared tooling primitives") extended to cover shared
reference knowledge, not only executable or client-installable
mechanism files. Keeps the existing `rules/` semantic intact rather
than overloading it.

## Consequences

- README.md's "Scope" section (previously: "the seven files... this
  repository is the mechanism only") is updated to describe two
  categories — mechanism (four directories, seven files, unchanged)
  and reference documentation (`docs/reference/`, one file today) —
  in the same commit as this ADR.
- README.md's "Composition" section gains a new subsection describing
  `docs/reference/` and its consultation model (read in place; not
  copied to `~/.claude/`).
- `scripts/check_manifest.py`'s `COMPOSITION_DIRS` includes
  `docs/reference/`; `MANIFEST.txt` includes
  `docs/reference/documentation-rules.md`.
- Every consuming project (article-pipeline, Radar, future Brain) may
  be told this new category exists, but each project decides
  independently whether and how to reference it — this ADR does not
  mandate that any consumer immediately vendor a local copy.
- `docs/adr/` itself is unaffected: ADRs remain a separate category
  from `docs/reference/`, not merged into it.

## Confirmation & Revisit

Not yet exercised end-to-end in a real consumer project — Radar is the
first project this will be pointed at, tracked as a separate,
following task, not part of this ADR.

Revisit if a second reference document is added and `docs/reference/`
needs a naming or indexing convention beyond a flat file list, or if a
consumer's actual usage pattern shows the "read in place, not copied"
consultation model doesn't hold in practice.

## Source

Owner decision, 2026-08-26, in response to a real placement question
raised while adding the Documentation Rules to
`mikkiola/tooltempest`: `rules/` was the initially assumed placement,
found not to fit on inspection of `drift-control.md`'s actual
frontmatter semantics and README's own "Scope" section, and escalated
to the owner rather than defaulted silently.
