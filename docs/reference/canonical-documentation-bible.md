# Canonical Documentation Bible

Applies to every project in this ecosystem (article-pipeline,
tooltempest, Radar, future Brain and others). Written in English per
Google Developer Documentation Style Guide, as required for all
technical artifacts across these projects.

This is a compiled reference, not a new invention — every rule below
traces to a decision already made in a real session (article-pipeline
or tooltempest), not a fresh design. Where a rule is still open or
project-specific, that is marked explicitly.

---

## The chain of authority — read this first

```
CONSTITUTION   → how to work
ARCHITECTURE   → what exists right now
ROADMAP        → where we're going
BACKLOG        → what to do next, concretely
SPEC.md        → exact task specification (transient, one active at a time)
ADR            → why a decision was made (permanent, one per decision)
```

**Single source of truth per level.** No fact lives in two documents.
If a fact could go in two places, it goes in exactly one — usually the
most specific one — and the other document points to it instead of
repeating it. This is the single most important rule governing all six
document types: violating it causes documentation drift, where an
agent updates one copy and forgets the other, and after a few sessions
the two copies silently disagree.

---

## 1. CONSTITUTION.md

**Purpose:** how work happens in this repo — role, protocols, rules for
the agent. Governs behavior, not content.

**What must NOT be here:** anything about what the product itself does
(→ ARCHITECTURE.md), anything about the plan (→ ROADMAP.md), anything
about open questions (→ BACKLOG.md). If a rule describes what the
*product* must always do, it doesn't belong here — it belongs in
ARCHITECTURE.md as a stated design constraint, or in an ADR as the
decision that produced it.

**Structure — every CONSTITUTION.md answers these questions, in this
order:**
1. **Role** — what is Claude/the agent doing in this project, at what
   level of autonomy.
2. **Session protocol** — what happens at the start of a session (what
   gets read, what gets stated before work begins), and — where
   applicable — the project's explicit session-end mechanism (e.g. an
   owner-triggered command, not autonomous agent judgment; autonomous
   "session is over" detection has been tried and found impractical in
   practice — see article-pipeline's [B-043]).
3. **Keeping documents current** — the autonomous-update rule: whenever
   a task's outcome makes ARCHITECTURE.md/ROADMAP.md/README.md content
   stale, the agent updates it directly as part of that task, not as a
   separate later step. Includes when the agent may create an ADR
   autonomously (when canonical docs/existing ADRs/the task itself
   provide sufficient basis) versus when it must stop and ask.
4. **The one stop-and-ask rule** — the single, explicit condition under
   which the agent must pause and ask the owner rather than deciding
   unilaterally (typically: a decision with genuinely different
   possible outcomes and no basis to choose between them, or a decision
   that reaches outside the task's stated scope).
5. **Test-Driven Development** — not a blanket requirement. Required
   whenever the task's own risk profile justifies it: correctness can't
   be cheaply verified by inspection, a wrong implementation would be
   expensive to discover later, or the mechanism's entire job is making
   a judgment call under specific conditions (e.g. a discovery/parsing
   function).
6. **Unconditional rules (no exceptions)** — sensitive operations
   (`git push`, token revocation, deleting files other processes may
   depend on) run only from the real machine, never from a sandboxed
   or browser-based environment; before introducing a new
   component/capability, verify no existing one already covers the
   responsibility (an actual search — grep, check docs/adr/, check the
   relevant package registry — not an assumption); any diff changing
   operation state, success/failure semantics, thresholds, or
   prompt/instruction structure must be preceded by a codebase-wide
   search for related references.
7. **ToolTempest consumer obligation** — mandatory, copied verbatim (or
   near-verbatim) into every project consuming `mikkiola/tooltempest`.
   States: whenever a session works on ToolTempest itself and
   adds/removes/renames a file under `scripts/`, `schemas/`, `skills/`,
   or `rules/`, that session must run ToolTempest's completeness-check
   script (`scripts/check_manifest.py`) and update `MANIFEST.txt` in
   the same commit if it reports a mismatch. Repinning
   `.tooltempest.lock` to pick up the change is a separate, deliberate
   action, never automatic.
8. **Conditional rules** — how the agent handles mid-task scoped
   decisions (proceed and report vs. stop and ask), and how external
   AI cross-checks must be drafted (neutrally, without steering toward
   the project's already-taken position).
9. **Write/Delete/Move confirmation** — every such action anywhere
   requires the explicit format: `[ЗАПРОС] Действие: X. Путь: Y.
   Подтвердить? (Y/n)`, and waits for the answer.
10. **Response format by task type** — how different task types
    (read-only audit, implementation, review) should be reported back.
11. **Review report format** — the exact shape of a completion report
    (commands run, literal output, commit SHA if committed).
12. **The `/spec` skill** — when it triggers (architectural decisions,
    anything not verifiable after the fact just by reading the result),
    when it doesn't (prose, drafts, brainstorms), and its model/auth
    restriction if one exists (e.g. "Sonnet only, existing Pro OAuth
    login, never a direct API key — reason: a prior uncontrolled-spend
    incident from an unconfirmed model/auth switch").
13. **`SPEC.md`'s status** — single root-level file, living/overwritten
    by each new `/spec` session (not accumulated), full history
    recoverable via `git log`, not kept in the file itself. If the
    project intentionally keeps SPEC.md permanently in the repo rather
    than deleting it after each cycle, that deviation is stated
    explicitly here (Radar's decision) — the default is Living Spec.
14. **ADR discipline** — see the full ADR section below; this section
    of CONSTITUTION.md states the citation rule (no ADR-number citation
    in ARCHITECTURE.md/ROADMAP.md/CONSTITUTION.md — describe the
    outcome, don't cite the number — with BACKLOG.md exempted, since
    it's a task log/history journal, not an architectural description)
    and the Immutable Lineage principle (ADRs are never edited after
    acceptance; a changed decision becomes a new, superseding ADR).
15. **Claude Code task discipline** — every task given to Claude Code
    states scope, out-of-scope, what must not be touched, and the exact
    report-back format. Read-only tasks are explicitly labeled as such.
16. **Language** — see the Language section below.

**How to maintain it:** edited rarely, deliberately, by explicit
owner-directed change — not touched as a side effect of routine work.
A new rule is added when a real recurring failure mode is found (e.g.
"CHECKPOINT.md orphaning recurs" led to ADR-0037's deprecation, which
then justified removing the corresponding CONSTITUTION.md assumption).
Never touched by the session-end auto-close/sync mechanism — explicitly
and permanently excluded from that automation, in every project, by
design (encoding *how to work* is not a mechanically-diffable fact the
way a component's status is).

**Rules for edits:** treat as append-mostly. When a rule is superseded,
say so in place with a dated note rather than silently deleting the old
text — this document has a smaller blast radius than an ADR but should
still show its own history of change, not present every version as if
it were the only one that ever existed.

---

## 2. ARCHITECTURE.md

**Purpose:** current state and dependencies of the system — what
exists right now, its status, what it depends on, how it was validated,
which commit produced it. Nothing else.

**What must NOT be here:** task instructions or requirements (→
BACKLOG.md), plan/sequencing (→ ROADMAP.md), rationale for why a
decision was made (→ docs/adr/ — cite the outcome, never the ADR
number). This document does not duplicate what those own.

**Structure — every ARCHITECTURE.md answers:**
1. **What components exist**, one row per component, in a single table
   with these exact columns: `Component | Status | Depends on |
   Validation | Commit`.
   - `Status`: one of `Implemented`, `Not started`, or a specific
     stated fraction/caveat (e.g. "M1-M5 complete; X not yet resolved")
     — never a vague word like "in progress" without saying what
     fraction or what's missing.
   - `Depends on`: the other component(s) or external repo this one
     needs, by name — not a prose explanation of the dependency.
   - `Validation`: what concretely confirms this component works —
     "tested on real data," "N live cases tested, M/N result," a named
     test suite — not "should work" or "looks correct."
   - `Commit`: the SHA(s) that produced the current state, so a reader
     can `git show` it directly. If the component's source is vendored
     from another repo, state both the source repo's commit and the
     vendoring commit into this repo.
2. **What repositories this system spans**, if more than one — a
   second small table: `Repo | Contains`.
3. **What models/external services are used**, if relevant — a third
   small table: `Component | Model or service`.

**Format:** table cells only, no prose sections, as a stated,
enforced principle at the top of the file (a short preamble explaining
what this document does and doesn't cover is the one permitted
exception — even that preamble should stay under ~5 lines).

**How to maintain it:** direct-write, no confirmation gate. Whenever a
task changes a component's real status, the agent updates this file's
row directly as part of that same task/commit — not a separate,
later, manually-prompted step. This is the "autonomous update" case
CONSTITUTION.md's session-protocol section requires. A component
directory change not paired with an ARCHITECTURE.md update in the same
push should trigger at least a warning (a pre-push check), even if that
check stays warn-only rather than hard-blocking, appropriate for a
solo-developer repository.

**Rules for edits:** every row must currently be true; this is not a
historical record — if a component's status changes, the row changes,
it doesn't get a new row alongside the old one. History lives in `git
log` for this file, not inside the file itself.

---

## 3. ROADMAP.md

**Purpose:** phases, sequencing, dependencies, and the current
execution pointer — nothing else. A short navigational layer, not a
task list.

**What must NOT be here:** task instructions, requirements, acceptance
criteria, or implementation detail (→ BACKLOG.md) — the moment a
roadmap item becomes concrete enough to actually start work on, it
moves to BACKLOG.md and ROADMAP.md keeps only a one-line pointer to the
phase it belongs to. No rationale (→ docs/adr/).

**Structure — every ROADMAP.md answers:**
1. **What phases exist and their status** — one table: `Phase | Status`.
   Status per phase is one of: `Closed`, `Not started`, or a specific
   blocked/unblocked state that must stay internally consistent with
   any prose elsewhere in the same file describing the same phase (a
   table row saying "Blocked" while a "Current pointer" paragraph says
   "no longer blocked" is a real defect, not a stylistic choice — this
   exact contradiction has occurred in practice and was fixed as a bug,
   not a redesign).
2. **Current pointer** — where work stands right now and what a
   session could reasonably pull next. This section legitimately
   contains prose, not forced into a table: current-state facts within
   it should stay short and could in principle become frontmatter
   fields, but any genuinely advisory/recommending sentence ("next work
   can pull from X or begin Y") is not a fact about the system and
   should not be tabulated — cross-checked externally (3 independent AI
   research passes, plus Google's own technical-writing guidance and
   Google's Open Knowledge Format, June 2026) and confirmed: "remove
   ALL prose" is not supported practice for architecture/roadmap docs;
   the supported middle ground is facts/state → tables or frontmatter,
   causal explanation and advisory content → brief prose reserved
   specifically for what a table would represent losslessly. Do not
   force an advisory sentence into a table cell to satisfy a
   table-only aesthetic — that misrepresents a discretionary
   recommendation as a deterministic fact.
3. **Dependency chain** — a simple diagram (ASCII arrows in a fenced
   code block is an established, acceptable form) showing the sequence
   phases actually depend on each other in.
4. **Open decisions** — a one-line pointer to BACKLOG.md's "Owner
   decisions needed" section, not a duplicate list.

**How to maintain it:** direct-write, no confirmation gate, same as
ARCHITECTURE.md — a phase's status or the Current Pointer's content
changes as part of whatever task changed the underlying fact, not as a
separate manual step. No entry-level IDs on ROADMAP.md items (macro-
phase granularity only) — if fine-grained tracking is needed for a
specific line, that line has already become a BACKLOG.md task and
should move there.

**Rules for edits:** keep it short — if ROADMAP.md is growing long
enough to need its own internal navigation, that is a sign task detail
has leaked in from BACKLOG.md and should be moved back out.

---

## 4. BACKLOG.md

**Purpose:** open tasks and owner decisions needed — everything
concrete enough to act on, in priority order.

**What must NOT be here:** rationale for a past decision once it's been
made (→ docs/adr/, cite the outcome only, since BACKLOG.md's own
citation exemption is specifically for referencing past ADR numbers as
task-log history, not for encoding new rationale). Current system state
(→ ARCHITECTURE.md). High-level plan/sequencing (→ ROADMAP.md).

**Structure — every entry answers:**
1. **A stable, unique ID**, inline on the heading line: `### [B-NNN]
   P<priority> — <one-line description>`. IDs are assigned sequentially
   (never reused, never renumbered) and survive retitling — if an
   entry's scope changes enough that the old ID no longer describes it
   accurately, that closes the old ID and opens a new one, rather than
   silently repurposing the number.
2. **Priority** (`P0`/`P1`/`P2`/`P3` or an equivalent scheme) —
   consistent across the whole file, sorted or at minimum grep-able by
   priority.
3. **When and how it was found** — a `Found: <date>, <context>` line,
   so a later reader can trace why this entry exists without having to
   ask.
4. **The problem itself**, in enough detail that someone with no other
   context can understand what's wrong or missing and why it matters.
5. **A checklist of concrete next steps** or an explicit statement that
   it's deferred/not being fixed now and why (a real, stated reason —
   "P3, no current trigger" is acceptable; silence is not).
6. **A `**Source.**` line** — where/when/how this entry was decided or
   found, for provenance. If an external AI or a prior session
   contributed to this entry's content and that content couldn't be
   independently verified against the actual repo, the entry must say
   so explicitly rather than presenting synthesized/unverifiable
   content as established fact.

**BACKLOG.md is explicitly exempted** from the "no ADR-number citation
in prose" rule that applies to ARCHITECTURE.md/ROADMAP.md/
CONSTITUTION.md — it is a task log/history journal, not an
architectural description, so citing which ADR resolved or relates to
an entry is appropriate here specifically.

**How to maintain it:** confirmation-gated, unlike ARCHITECTURE.md/
ROADMAP.md — closing an entry (marking done, adding `— RESOLVED` to its
title) requires either the owner's explicit go-ahead in the moment, or
an explicit session-end trigger the owner themselves invokes (never an
agent's autonomous judgment that "the session seems complete" — that
detection has been tried and found structurally unreliable across
multi-task sessions). Closure is signaled in the closing commit via a
structured trailer (e.g. `Closes: B-NNN`, one trailer line per entry if
a single commit closes more than one) — a real git trailer, in the
commit's trailing trailer block, not a mention of the entry number
anywhere else in the commit message; verify this with git's own
trailer parser (`git log --format="%(trailers:key=Closes,valueonly)"`),
never a naive text search, since ordinary prose discussing an entry by
number can otherwise produce false positives.

**Rules for edits:** if a closure later turns out wrong, an ordinary
follow-up edit/commit fixes it — no supersession ceremony (that's an
ADR-specific requirement, not a BACKLOG.md one). Splitting one entry
into several (e.g. because it referenced external material that turned
out not to exist, or its scope grew) is legitimate — resolve the
original with a note explaining the split and pointing to the new
entries' IDs, sourced to what the entry's own text actually established
rather than to unverifiable external material.

---

## 5. `docs/adr/` (Architecture Decision Records)

**Purpose:** why a decision was made — permanent record of context,
options considered, what was chosen, and consequences. One file per
decision, never edited after acceptance.

**Format — MADR-style, 6 blocks plus a metadata line, per file:**

```markdown
---
id: ADR-NNNN
status: Accepted | Proposed | Deprecated | Superseded
supersedes: null | ADR-MMMM
superseded_by: null | ADR-MMMM
source_type: verbatim | inferred
---

# ADR-NNNN: <Title>

## Status

Accepted (or whichever status, with date/commit if relevant)

## Context & Constraints

What problem or tension prompted this decision. Real constraints that
bounded the possible answers.

## Decision

What was actually decided, stated plainly.

## Alternatives & Rationale

The options considered (as a table, if that's clearer), which was
chosen and why, and — critically — WHY THE REJECTED ONES WERE REJECTED.
This is the single most load-bearing part of the whole record: the
reasoning trail that lets someone later understand why NOT to revisit
an already-rejected option without new information.

## Consequences

What actually changes as a result — for the system, for future
decisions, for anything this decision constrains going forward.

## Confirmation & Revisit

How this was verified (test output, a real command run, a real
scenario checked) if verification is meaningful for this kind of
decision — not every ADR needs this to be a script; a design decision
can be "confirmed" by the reasoning holding up under a cross-check
instead. State explicitly what would be grounds to revisit this
decision later, so a future reader can tell "still valid" from "this
assumption no longer holds" without re-deriving the whole argument.

**Source.** One line: which session/context this decision came from,
including if it was informed by external AI cross-checks (name how
many, whether they were unanimous) or if it came from a prior,
undocumented session being formalized after the fact.
```

**Note (added when this Bible was vendored to tooltempest, 2026-08-26):**
tooltempest's own ADRs (0001-0008) do not carry the `---` frontmatter
block shown above — they use the same six/seven prose blocks (`Status`
/ `Context & Constraints` / `Decision` / `Alternatives & Rationale` /
`Consequences` / `Confirmation & Revisit` / `Source`) starting directly
from the `# ADR-NNNN: Title` heading, with no YAML metadata. This is a
per-project formatting choice, not a violation of this Bible's format
— see this document's own "Existing ADRs are not retroactively
migrated" rule below. A new ADR in any project follows whichever
concrete shape that project's own most recent accepted ADR already
uses; check it directly before writing, don't assume this Bible's
literal frontmatter example is mandatory where a project has already
established a working alternative.

**This format was reached deliberately** — not the original 11-field
format some projects started with (too much semantic duplication
between fields like Why/Constraints and Options/Chosen/Rejected), and
not a stripped 4-field format either (loses the alternatives/rejection
reasoning trail that MADR practice and multiple independent sources
agree is essential for reconstructability — the actual goal this whole
document system serves). 6-7 fields is the settled compromise,
cross-checked against three independent sources plus established MADR
organizational practice.

**Numbering:** sequential per repository, never reused, never
renumbered retroactively — this repo's own sequence is independent of
any other repo's ADR numbering (a consuming project and the tooling
repo it consumes both number their own ADRs starting from 0001; do not
conflate the two sequences). Re-check the actual current highest number
in `docs/adr/` immediately before assigning a new one — don't assume a
number is still free if time has passed since a plan was drafted.

**Immutable Lineage:** an ADR is never edited after acceptance, not
even to fix a typo in its reasoning. A changed decision becomes a NEW
ADR that supersedes the old one — the old file's `status:` field and
body text stay exactly as originally accepted, as the historical record
of what was actually decided and why, even after it's superseded. The
new ADR's frontmatter sets `supersedes: ADR-<old-number>`; the old
ADR's `superseded_by` field is the one exception to "never edited" —
it may be updated to point forward, since that's pure cross-reference
metadata, not a change to the decision's own content. `git rm` on an
ADR file has no standing precedent in this project's history and
requires explicit, separate owner authorization if ever proposed — it
is not a routine cleanup action.

**When Immutable Lineage doesn't apply:** a construction-phase
migration into this format (e.g. converting a batch of pre-existing
ADRs from an older field format into this one) is a stated, explicit,
temporary exception, not a precedent for future casual rewrites — the
exception covers reformatting existing content into the new field
shape, never deleting or reversing what was actually decided.

**Existing ADRs are not retroactively migrated** when the format itself
changes (e.g. moving to this 6-block shape from something older) —
new ADRs use the new format going forward; old ones stay in whatever
format they were accepted in, readable as the historical record they
are. Migrating them is a separate, deliberate, explicitly-scoped task
if it's ever undertaken, not an automatic consequence of adopting a new
format.

**When Claude/the agent may write an ADR autonomously (per
CONSTITUTION.md's rule):** whenever canonical docs, existing ADRs, or
the task itself provide a sufficient basis to choose one outcome over
genuine alternatives — this applies even to decisions about
CONSTITUTION.md itself, ARCHITECTURE.md's component list, or ROADMAP's
phase sequencing. The ADR records the decision as part of the same
task/commit that implements it, not as a separate later approval step.

**Citation rule:** ARCHITECTURE.md, ROADMAP.md, and CONSTITUTION.md
describe decisions in prose without citing a specific ADR number — "the
component is vendored, not a single source of truth yet" rather than
"per ADR-0014." If a future edit reintroduces a number citation in any
of these three, that's a violation to fix in that file, not a reason to
loosen the rule. This is enforced mechanically by a destination-
invariant script wired into a pre-push hook, not left as a prose-only
expectation. BACKLOG.md is exempt (see BACKLOG.md's own section above).

---

## 6. `docs/adr/ADR-INDEX.md`

**Purpose:** a generated, browsable index of every ADR — number,
title, status, supersession relationships — so a reader doesn't have to
open every file to find the one they need.

**This file is a build artifact, not hand-authored.** Generated by a
script (e.g. `scripts/generate_adr_index.py`) from the actual
frontmatter and headers of every file in `docs/adr/`, never edited by
hand. It is exempt from the `NNNN-slug.md` filename convention that
numbering-consistency checks apply to every other file in that
directory, and exempt from the numbering sequence itself (it is not
itself a numbered ADR).

**When to build one at all:** justified once a project's ADR count
reaches a scale where manually scanning `docs/adr/` to find a specific
decision becomes real friction — not before. Building this generator
speculatively, before a project has enough ADRs for it to matter, is
premature engineering; a project with a handful of ADRs does not need
one yet, and that is a legitimate, deliberate decision to defer, not an
oversight.

**How to maintain it:** regenerated by running its script, ideally
wired into the same workflow that adds/edits ADRs (a git hook or a
step in the ADR-authoring process) so it never silently goes stale
relative to the real files in `docs/adr/`. If it isn't wired into
automation yet, that gap should be a stated, tracked BACKLOG.md item,
not a silent assumption that it's kept current by memory.

**Rules for edits:** none, directly — all edits happen by re-running
the generator against the current state of `docs/adr/`. If the
generated output looks wrong, the fix is in the generator script or in
the source ADR files' frontmatter, never a hand-patch to
`ADR-INDEX.md` itself.

---

## README.md — a note, not a seventh canonical document

README.md gets the same "autonomous update, no confirmation gate"
treatment as ARCHITECTURE.md and ROADMAP.md — whenever a task's outcome
makes README.md's content stale, it's updated directly as part of that
task. This does NOT make it a fifth/seventh canonical top-level
document with its own dedicated rules section; it's governed by the
same principle CONSTITUTION.md states once, applied to one more file.

---

## CHANGELOG.md — deliberately does not exist

Two real precedents exist in this ecosystem, with different reasoning,
which is worth knowing before deciding for a new project:

- One project **kept** a curated CHANGELOG.md, on the reasoning that
  raw git log doesn't fully substitute for a curated, readable summary.
- Another project **removed** CHANGELOG.md entirely, on the reasoning
  that `docs/adr/` (permanent decision records) plus git log together
  already capture everything a changelog would track, without a
  separate file that has to be kept in sync by hand.

**Default for new projects in this ecosystem: remove/don't create a
separate CHANGELOG.md** — follow the second precedent. `docs/adr/` plus
`git log` are the source of truth for what changed and why. Before
removing an existing CHANGELOG.md from a project that already has one,
read its actual content first and confirm nothing in it is neither
recoverable from git log nor naturally an ADR's content (e.g. pure
narrative notes with no corresponding decision or commit) — if such
content exists, flag it explicitly rather than assuming it transfers
cleanly, rather than deleting it silently.

---

## Language

**All technical documentation and code artifacts** — CONSTITUTION.md,
ARCHITECTURE.md, ROADMAP.md, BACKLOG.md, docs/adr/, code comments,
commit messages, prompts sent to Claude Code — strictly in **English**,
per the Google Developer Documentation Style Guide, no exceptions.

**Conversation with the owner** (architect chat) — Russian.

**The product's own output** — whatever language its actual audience
needs. This is a separate axis from the technical-documentation rule
above and does not get overridden by it: a project whose end product is
Russian-language content (e.g. Telegram posts) keeps that content in
Russian by design, and this must be stated as an explicit, named
exception in that project's own CONSTITUTION.md language section — not
left implicit, since an implicit rule risks a future session concluding
that literally everything, including the product's own output, must be
English.

**A concrete precedent for the product-output exception (Radar):**
human-facing output (chat with the owner, Telegram posts, `## Правка
человека` / `[Добавить вручную]` fields) is always Russian. New
vault/data files' body text and `##` headings are English, but specific
machine-parsed field labels (e.g. `Оценка`, `Статус`, `Вердикт`,
`Уверенность`) stay as Russian literals permanently, never translated —
because code parses them by exact string match. Old Russian-language
files are not retrofitted to the new convention retroactively.

---

## Verifying compliance after any migration or rewrite

Whenever a document is migrated to this format or substantially
rewritten, verify explicitly, as a separate, stated step — not assumed
complete just because the rewrite happened:

1. **Nothing was lost.** Every fact, decision, and open question present
   in the original is still present somewhere in the new structure —
   check this by re-reading the original source and confirming each
   piece of content has a home, not by trusting that the rewrite pass
   was thorough.
2. **The new structure is actually followed**, not just superficially
   similar — the right document has the right sections, in the right
   format, answering the right questions (per this bible's own
   structure for that document type) — not prose that happens to
   mention the right topics in the wrong shape.
3. **Cross-document consistency** — no fact contradicts itself between
   two documents (e.g. a status table and an adjacent prose section
   describing the same thing differently) — this exact class of defect
   has occurred in practice and is not hypothetical.
4. **Report the verification itself**, not just the rewrite — state
   explicitly what was checked and confirmed, so the check is visible
   and repeatable, not an unstated assumption a future reader has to
   trust blindly.
