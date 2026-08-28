# ADR-0009: Narrow the ROADMAP.md Dependency-Chain Diagram Exemption to a Table-First Rule

## Status

Accepted

Narrows, but does not reverse, ADR-0008. ADR-0008's own decision — creating
the `docs/reference/` Composition category and adding
`docs/reference/documentation-rules.md` to it — is unaffected and remains
in effect. What this ADR supersedes is a single sub-claim asserted in that
document's ROADMAP.md section (not in ADR-0008's own body text, which
never mentions diagrams): that "a simple diagram (ASCII arrows in a fenced
code block is an established, acceptable form)" is acceptable for
ROADMAP.md's "Dependency chain" section.

## Context & Constraints

The claim being narrowed is not literally part of ADR-0008's decision
text — ADR-0008 concerns the creation of `docs/reference/` as a new
Composition category and never discusses diagrams. The actual wording
lives in `docs/reference/documentation-rules.md` itself, the file
ADR-0008 introduced into this category, in its ROADMAP.md section, item
3 ("Dependency chain"). That document's own preamble states "every rule
below traces to a decision already made in a real session ... not a
fresh design" — but no dedicated prior ADR, in this repository or any
consumer's, specifically decided the ASCII-diagram-acceptable claim; it
entered the document as compiled reference prose without its own
traceable ADR. This ADR is therefore the first ADR to formally decide
this specific sub-question, not a revision of an earlier one.

A consuming project (`mikkiola/article-pipeline`) tested the claim
against its own real doc-sync automation and real ROADMAP.md/
ARCHITECTURE.md content, rather than taking it on trust, and found
(recorded in that project's `docs/BACKLOG.md`, entry `[B-053]`,
2026-08-28):

1. **No code path in this ecosystem's own doc-sync tooling parses,
   validates, or regenerates a fenced ASCII diagram.** `scripts/
   doc_sync_tier2.py` (this repository's own vendored Tier 2 write
   infrastructure) has no heading-aware or diagram-aware parsing
   anywhere in it — confirmed by direct read: it treats a gated
   document as opaque whole-file content (snapshot the prior state,
   diff the whole new content against it, write atomically on
   confirmation), exactly as ADR-0002/ADR-0003 specify. The consuming
   project's session-end skill's `Syncs:` trailer mechanism does the
   same for its own direct-write docs. Neither has, or was ever
   designed to have, any concept of a diagram's internal structure.
2. **The diagram had, in practice, already drifted from fact.** The
   consuming project's ROADMAP.md diagram drew a single linear
   dependency chain, while that same project's ARCHITECTURE.md "Depends
   on" column — the acknowledged source of truth for this fact — showed
   two parallel branches converging on a later phase, not a linear
   sequence. This was a real, live, previously undetected mismatch,
   confirmed by direct comparison, not a hypothetical risk raised in the
   abstract.
3. **The mismatch was not recent.** `git log -p` on that section showed
   every historical edit only ever changed status labels inside existing
   diagram nodes (e.g. "current" → "closed") — the graph's actual
   shape/edges were never revisited or re-derived from ARCHITECTURE.md
   since the diagram's original commit. The diagram survived only
   because a human or Claude Code happened to remember to hand-edit it,
   which nothing in this ecosystem's tooling ever confirms happened.

This undermines `documentation-rules.md`'s own stated goal for
ROADMAP.md: facts/state belong in tables or frontmatter specifically
because those are mechanically diffable and checkable, while free
prose/diagrams are reserved for genuinely advisory or non-tabular
content. "Which component depends on which" is exactly the class of
fact ARCHITECTURE.md's own `Depends on` column already expresses
losslessly elsewhere in this same document set. A diagram that nothing
in the actual pipeline keeps synchronized is, empirically, exactly as
drift-prone as the untracked prose the table-first principle exists to
eliminate — the diagram exemption was giving dependency-chain content
the one property (silent, unchecked drift) the rest of ROADMAP.md's
structure was designed to prevent.

## Decision

Narrow the ROADMAP.md "Dependency chain" section's acceptable form:

Dependency/state information (which phase or component depends on
which) must be expressed as a table — `Phase or Component | Depends
on` — sourced from, and kept consistent with, ARCHITECTURE.md's own
`Depends on` column for the same entities.

A fenced diagram remains acceptable in this section ONLY for content
that has no lossless tabular equivalent — e.g., an actual algorithm or
process flow with branching or looping structure a two-column table
cannot represent without becoming unreadable. A simple parent/dependency
list — which is what every dependency chain in this ecosystem has
actually been, to date — is not that kind of content and must use a
table.

This is a narrowing, not a reversal, of "diagrams are sometimes fine."
`documentation-rules.md`'s general position on ROADMAP.md's "Current
pointer" section — that "remove ALL prose" is not supported practice —
stands unchanged. This ADR removes only the specific carve-out for
dependency-chain diagrams, which was never actually a case where a
diagram was the right tool for the content.

## Alternatives & Rationale

**Leave the exemption as-is; treat the drift finding as a one-off
documentation bug in the consuming project** — rejected. The drift was
not caused by a one-off authoring mistake; it was caused by the absence
of any mechanism, anywhere in this ecosystem's tooling, capable of
catching it. That structural gap exists identically in every consumer
of this Bible, so the same failure mode will recur elsewhere.

**Teach doc-sync tooling to parse/validate the diagram instead of
changing the rule** — rejected. `doc_sync_tier2.py`'s deliberate design
(ADR-0002, ADR-0003) treats gated documents as whole-file, human-
reviewed diffs. Adding diagram-aware parsing would be new, non-trivial
scope with no other identified need, solely to preserve a diagram
format when a table already expresses the same fact losslessly and
diffably with zero new tooling.

**Ban diagrams from ROADMAP.md entirely** — rejected as broader than the
finding supports. The finding is specific to dependency/state facts,
which have a tabular equivalent; it says nothing about content that
genuinely lacks one. A blanket ban would reintroduce the "table-only
aesthetic forcing a lossy fit" problem `documentation-rules.md`'s own
"Current pointer" section already rejected for advisory prose — narrow
the exemption to match the actual failure mode instead of removing it
wholesale.

**Narrow the exemption to table-first for tabular-equivalent content
only** — chosen. Matches the actual failure mode (an unmaintained,
drift-prone diagram expressing a fact a table already expresses
losslessly and diffably), preserves the general "diagrams are sometimes
fine" position for content that genuinely needs one, and requires no
new tooling.

## Consequences

- `docs/reference/documentation-rules.md`'s ROADMAP.md section, item 3
  ("Dependency chain"), is updated in the same commit as this ADR: the
  sentence endorsing "ASCII arrows in a fenced code block" as an
  "established, acceptable form" is replaced with the table-first rule
  above, plus a pointer to this ADR for the rationale.
- Any consuming project maintaining a ROADMAP.md "Dependency chain"
  section as a diagram should convert it to a table sourced from its own
  ARCHITECTURE.md, as a follow-up task in that project — this ADR does
  not itself edit any consumer's ROADMAP.md, which is out of scope for a
  ToolTempest-side ADR. `article-pipeline` already made this change
  locally in its own repository ahead of this ADR (`[B-053]`).
- ADR-0008's own decision — the existence of `docs/reference/` as a
  Composition category, and `documentation-rules.md`'s place in it — is
  unaffected and remains in effect.
- No change to `MANIFEST.txt` or `scripts/check_manifest.py`'s
  `COMPOSITION_DIRS`: `documentation-rules.md` is not renamed or moved,
  only its content is edited in place.

## Confirmation & Revisit

Confirmed by a concrete finding, not a hypothetical: a real consumer
project (`article-pipeline`) tested the prior exemption against its own
actual doc-sync tooling and its own actual ARCHITECTURE.md/ROADMAP.md
content, and found a live, previously undetected drift caused by
exactly the structural gap this ADR closes (see Context & Constraints
above, and `article-pipeline`'s `docs/BACKLOG.md` entry `[B-053]`,
2026-08-28).

Revisit if a future dependency-chain use case genuinely cannot be
expressed as a table (e.g., conditional/branching dependencies a
`Depends on` column can't represent without becoming its own free-text
mini-language) — at that point the exemption's boundary may need to be
more precise than "no tabular equivalent." Also revisit if this
ecosystem's doc-sync tooling is later extended to actually parse and
validate fenced diagrams, which would remove the drift risk that
motivates this ADR and could justify reconsidering the narrowing
itself.

## Source

Owner decision, 2026-08-28, in response to a concrete finding
(`article-pipeline`'s `docs/BACKLOG.md` entry `[B-053]`) filed by a
consumer project as evidence for a future ToolTempest-side session to
weigh, per that entry's own stated scope note ("out of scope for this
entry ... filed as evidence ... not an instruction to change ToolTempest
unilaterally from here"). This ADR is that follow-up session.
