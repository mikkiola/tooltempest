# ADR-0013: Shared DocOps Checker — Frontmatter State Declaration, Deny-List Body Check, Snapshot-Bound Read-Only Run, Per-Dimension Result

## Status

Proposed, 2026-10-21. Implements Stage 1 of the DocOps tooling redesign
(pure shared parser, policy, tree reader and error schema). Becomes
Accepted when the Owner accepts the decisions below. Does not supersede
or modify any earlier ADR; `scripts/doc_sync.py`, `scripts/doc_sync_tier2.py`
and the consumers' copies of `verify.py` are untouched and keep working
until a later stage retires them.

This record replaces two earlier drafts of the same decision. The first
put the inactive marker in the SPEC body as an HTML comment; three
independent review rounds each found new false-PASS or false-N/A paths,
all from one cause: finding the marker needed Markdown parsing of the
body. The second moved the marker to frontmatter but still tried to
place fences, comments, quotes and lists exactly; three further review
rounds found more of the same, because any such placement logic can be
fooled. The Owner then chose the design below: declare state in
frontmatter, and parse the body only if it contains none of the
constructs the checker cannot place (Decisions 1 to 5).

## Context & Constraints

The approved unified SPEC policy for ToolTempest, Article Pipeline, Brain
and Radar has four cases: no SPEC (normal, N/A); a SPEC that explicitly
states there is no active task (same state, N/A); a SPEC for active work
(PASS if it meets the common structural contract, FAIL if not); and a
SPEC or checker that is corrupted, unreadable, unavailable or failed to
run (never N/A, never PASS). The policy left one design question open:
a machine-readable marker that separates the second case from the
fourth.

What the replaced code does, from the DocOps investigation (report §C,
§D, §G): the consumers' `scripts/verify.py` (byte-identical in Article
Pipeline and Radar, plus ten docstring lines in Brain) reads the working
tree, treats "no recognised checklist" as `UNKNOWN` for both an
intentionally idle SPEC and a damaged one, crashes on unreadable
encodings, ignores invalid checkbox spellings, guesses component names
from the checkout directory, and silently skips a SPEC nested more than
one directory deep. `doc_sync.py` then treats "exit 2 and no JSON" as "no
SPEC" (so a crash can pass), validates the working tree instead of the
staged or pushed content, and re-scans before recording. ToolTempest
itself has no production `verify.py`; only the CHECKPOINT-oriented stub
in `tests/fixtures/` exists here.

Checked on 2026-10-21 at the consumers' HEAD: none of the committed
SPEC.md files in article-pipeline, Brain (root and `verification/`) or
Radar has frontmatter (each starts with a `# Title` line and contains no
`---` line). ADR files in all of them use the `---` / flat `key: value`
/ `---` convention. Introducing frontmatter to SPEC.md is therefore a new
structural element for the consumers, not an existing one.

Constraints: standard-library Python only; no project-specific
directories or fabricated SPEC canon inside ToolTempest; no mutation,
network or model call when a check runs; consumers are not migrated and
no lock is repinned in this stage.

## Decision

1. **State is declared in frontmatter, found without Markdown parsing.**
   A SPEC declares itself inactive with

       ---
       spec-state: idle
       ---

   at the very top of the file. Frontmatter exists only if the first
   line is exactly `---`; it ends at the next line that is exactly
   `---` (recognition is exact, not loose). A first non-blank line that
   looks like a delimiter but is not exactly `---` on line 1 (`--- `,
   `----`, ` ---`, `--- spec-state: idle`, or a `---` after a blank line;
   in short, a first non-blank line that begins with three or more
   dashes without being exactly `---` on line 1) is a FAIL
   (`FRONTMATTER_NOT_AT_START`), so a declared `idle` can never be lost
   by a stray character and silently read as active. Between the
   delimiters every line must be blank or flat `key: value` (a key of
   letters, digits, `_`, `-`; a colon followed by a space, tab or end of
   line). Nesting, lists, comments and non-ASCII whitespace are
   rejected. Delimiters are found by exact string comparison and key
   lines by one fixed pattern; this is not a YAML parser and uses no
   dependency. Other keys are allowed and ignored, so a SPEC may carry
   the same metadata style as ADR files. The body is never searched for
   the declaration.
2. **Two-phase state determination.**
   Phase 1 reads frontmatter only. `spec-state: idle`, present exactly
   once and well formed, is N/A, full stop: the body is not parsed (an
   idle SPEC that still holds milestone items, or any denied construct,
   is also N/A; this follows the Owner's decision and is a deliberate
   consequence). No frontmatter, frontmatter without the key, or
   `spec-state: active` means active and phase 2 runs. Frontmatter that
   is present but malformed is a FAIL and never "no marker, so active":
   unterminated (no closing `---`), a non-flat line, a duplicated
   `spec-state` (even if identical), an unknown value (anything but
   exactly `idle` or `active`, so `Idle`, `inactive` or a quoted value
   fail), or a near-miss key (`spec_state`, `Spec-State`). Undecodable
   bytes are a FAIL before phase 1.
3. **Body check (phase 2): deny-list first, structure second.** The body
   is parsed for structure only if it contains none of the following
   anywhere in the document. The frontmatter region and the interior of
   correctly closed top-level fences are excluded; nothing else is
   scoped, because scoping the deny-list to one section would depend on
   the same boundary detection this design stops trusting. Any hit is a
   FAIL naming the construct and the line (`DENIED_*` codes); the body
   is then not parsed for structure, and at most 100 problems are
   listed.
   - an HTML comment token, `<!--` or `-->` (`DENIED_COMMENT`);
   - an HTML tag: `<` followed by a letter, `/`, `!` or `?`, anywhere in
     a line, including inside backticks, autolinks and `<placeholder>`
     text (`DENIED_HTML`); a lone `<` or `<=` in running text is fine;
   - a line indented four or more columns, a tab counting as four
     (`DENIED_INDENT`); blank lines are not indentation;
   - a blockquote marker, `>` after at most three spaces
     (`DENIED_BLOCKQUOTE`); `>` inside a line is fine;
   - an invisible or non-ASCII whitespace character anywhere in a line:
     NBSP and other spaces, zero-width and other format characters (U+200B,
     U+2060, a second BOM, bidi controls), line and paragraph separators,
     control characters other than tab (`DENIED_INVISIBLE`); ordinary
     non-ASCII text is fine;
   - a setext heading underline, `===` or `---` directly under a
     non-blank text line (`DENIED_SETEXT`); a thematic break after a
     blank line or an ATX heading is fine;
   - a nested list: a list marker repeated on one line (`- - x`), a
     heading inside a list item (`- ## x`), or any list item indented one
     to three columns (`DENIED_NESTED_LIST`);
   - a heading indented one to three columns (`DENIED_INDENTED_HEADING`),
     because Markdown may place it inside the preceding list item, where
     it would not end the Milestones section; headings must start at
     column 0;
   - a level-1 or level-2 heading whose title contains markup: `*`, `_`,
     a backtick, `[`, `]`, `&` or `<` (`DENIED_HEADING_MARKUP`). Markdown
     renders the title, so `## **Milestones**` or `## &#77;ilestones`
     is a second Milestones heading that a literal title match would
     miss, and a damaged item under it would go unchecked. Titles of
     level-3 and deeper headings are not restricted.
   Nothing about these constructs is interpreted: the checker does not
   decide whether a `<!--` sits in code, a quote or a list, which is the
   decision earlier designs got wrong.
4. **Fences: one simple supported form.** A fence opens with a run of 3+
   backticks or tildes at column 0 and closes with a run of the same
   character, at least as long, at column 0, followed only by spaces or
   tabs. The interior is excluded from the deny-list and from milestone
   counting, so a checkbox inside a closed fence is never a milestone.
   FAIL: a fence never closed (`FENCE_UNCLOSED`; a different character, a
   shorter run, trailing text or an NBSP all fail to close it); a fence
   that is indented or follows a list bullet (`FENCE_INDENTED`,
   `FENCE_NESTED`); a closing-looking run indented one to three columns
   (`FENCE_CLOSER_AMBIGUOUS`, because Markdown would close there). A run
   of triple backticks with a backtick in its info text is inline code,
   not a fence. The checker does not claim CommonMark conformance.
5. **Milestones section: an allow-list.** Inside the Milestones section
   (between its heading and the next level-1 or level-2 heading) every
   non-blank line must be one of: a well-formed checkbox item (below), a
   sub-heading (`###` and deeper, at column 0), or plain text. A line that
   starts like a list item without being a well-formed checkbox item (an
   empty bullet, `- text`, `- [text](url)`, `- [a][b]`, `- [TODO] x`,
   `- \[x]`, `- &#91;x]`, `- > x`), any numbered item, and any line
   starting with `[`, `\` or `&` is a FAIL (`DENIED_UNRECOGNIZED_LINE`,
   `DENIED_ORDERED_LIST`), because Markdown may render it as an item or
   structure the checker does not count, and a malformed item could hide
   behind it. The deny-list of Decision 3 closes constructs that move
   boundaries; this allow-list closes the other half, lines inside the
   section that the item counter would silently skip. Plain paragraphs,
   links and brackets inside prose, bold, emphasis and code spans are
   fine as text. This is a further narrowing of the format: a Milestones
   section holds checkbox items, sub-headings and prose, not other bullet
   lists or numbered lists.
6. **Structural contract for an active SPEC** (applied only when nothing
   was denied): exactly one level-2 `Milestones` heading (suffix only
   after whitespace, `(` or `:`; ATX headings only; case-sensitive),
   whose section, ended by the next level-1 or level-2 heading
   (level-3 subsections stay inside), holds at least one top-level
   checkbox item written with the bullet `-`, `*` or `+` followed by
   exactly one space or tab, a valid marker (`[ ]`, `[x]`, `[X]`),
   a space or tab, and non-empty same-line text. A list bullet followed by a bracket token of up to three
   characters is a checkbox attempt and must be well formed (`[]`, `[y]`,
   `[xx]`, `[~~]`, `- [ ]text` FAIL, so a status tag like `- [WIP] text`
   in Milestones fails by design); a bullet followed by `[` with no
   closing `]` on the line (`- [`, `- [x`, a cut-off last item) is
   `CHECKBOX_UNTERMINATED`; longer tokens and links (`[text](url)`) are
   plain list text; list markers use ASCII digits only. Checked or
   unchecked state is not judged. Checkboxes outside Milestones are never counted. Line endings
   may be LF, CRLF or CR; one UTF-8 BOM is allowed.
7. **Registry.** An optional `.docops-policy.json`, read from the
   inspected snapshot, registers SPEC tracks as `{path, track}` pairs
   (schema `schemas/docops-policy.schema.json`; any depth, strict
   validation, unknown keys rejected). It records where SPECs are, not
   whether they are active. Without it, a root `SPEC.md`, if present, is
   the single track `root`. A registered path absent from the snapshot is
   a FAIL; a directory at a SPEC path is a FAIL and at the policy path a
   tool failure, never "absent". A file named `spec.md` (case-insensitive)
   outside the registered paths is reported as an `OUT_OF_SCOPE_SPEC`
   warning (other spellings such as `SPEC.txt` are not detected). If such
   a file exists and nothing in scope was checked, or every in-scope SPEC
   is idle, the result is SKIP, not N/A ("no active task" is proven only
   for the registered tracks); with an empty registry and no `spec.md`
   anywhere it is N/A. With a PASS in scope the file stays a warning.
8. **Snapshot binding.** `scripts/docops.py check|report --tree
   index|<rev>` (repeatable) and `--push-stdin` read the index (honouring
   `GIT_INDEX_FILE`) or the named commit's tree through Git plumbing
   only, list the snapshot once and read content by blob OID, with
   `refs/replace` substitution disabled. The caller's `GIT_*` environment
   is inherited on purpose (a pre-commit hook relies on `GIT_INDEX_FILE`),
   so `GIT_DIR`, if set, decides which repository is read. For pushes,
   each tuple's local field must be a full hexadecimal OID; a deleted ref
   (all-zero OID) is N/A with an explicit reason. Annotated tags are
   peeled; non-commit objects, unknown names and unmerged or non-regular
   SPEC entries are never PASS. Git runs with optional locks and lazy
   fetch disabled and a timeout; the script disables bytecode caching.
9. **Status vocabulary and the error contract.** Every per-check status
   is exactly one of PASS, FAIL, SKIP, N/A, as in the approved Stage 0
   contract; no fifth value exists anywhere in the result. The result
   carries a separate field per dimension. `SPEC_STRUCTURE` has a
   `status` and the other six (`TESTS`, `DOC_IMPACT_ASSESSMENT`,
   `DOC_CONSISTENCY_REVIEW`, `ACCEPTANCE_VERIFICATION`, `PUBLICATION`,
   `OWNER_ACCEPTANCE`) are `{"assessed": false, "reason": ...}` with no
   `status` key at all: this version does not assess them, and nothing
   about them can be read from, or into, the structural verdict. The
   schema forbids a `status` on those six. A failure of the checker
   itself (Git cannot read the snapshot, an invalid policy, an
   unreadable blob, an internal error, a missing package, a bad
   request) is not a per-check status: the CLI exits 2 and reports an
   `error` object (`code`, `message`) on the affected snapshot, with
   `checks` and `status` null; top-level problems appear in
   `summary.reason`. Any tool failure makes `summary.status` null, so
   partial results are never summarised as a verdict. Exit codes: 0 PASS
   or N/A, 1 FAIL, 2 tool failure, 3 SKIP. A missing or broken package,
   an internal exception, an unwritable stdout and an empty request all
   exit non-zero. A command-line usage error (no `--tree`, unknown
   option) exits 2 with a message on stderr and no JSON document, since
   there is no snapshot to attach an `error` object to. In the text
   report, snapshot-derived text is escaped so control, format and
   lone-surrogate characters cannot reach a terminal.
10. **No `record` subcommand.** No current caller needs one; evidence
   recording belongs to the later impact-review stage.

## Alternatives & Rationale

| Option | Verdict |
|---|---|
| **Frontmatter `spec-state` field, flat `key: value`, string splits (chosen)** | The declaration is never searched for in Markdown body text. Same minimal convention as the ADR files, no parser dependency. Lives in the file whose state it describes, so the next `/spec` session that overwrites an idle SPEC wholesale removes it with the file and the SPEC is active again with no second place to update. |
| **Deny-list pre-pass, then structure (chosen for the body)** | Any construct the checker cannot place exactly makes the whole body a FAIL before anything is counted, so milestone detection never depends on section or container boundaries that a hidden construct could have moved. The deny-list is a closed set of cheap line tests, each independently testable. |
| In-body HTML comment `<!-- docops:spec-state=idle -->` (first draft) | Rejected after three review rounds: finding it correctly requires deciding what is inside a fence, comment, list or indented block, and every round found another way to read a damaged SPEC as idle or a valid one as damaged. |
| Place every construct exactly (fences in lists, comments, code spans, quotes) with FAIL only for the unplaceable (second draft) | Rejected after three more review rounds: each round found another input where the placement logic disagreed with Markdown (indented closers, backtick runs across lines, setext after a comment, CDATA, invisible characters). A grammar that must match Markdown's block rules is the part that does not converge. |
| **Allow-list inside Milestones (chosen)** | Review of the deny-list design found that lines the item counter skips (an empty bullet, `- \[x]`, `- [a][b]`, a numbered item after a paragraph) could hide a malformed item. Enumerating more of them is the non-converging path; requiring every line in the section to be a checkbox item, a sub-heading or plain text makes "the counter saw every item" true by construction. |
| Deny-list scoped to the Milestones section only | Rejected by the Owner: knowing where Milestones starts and ends depends on the same boundary detection. The list applies to the whole document. |
| `state: active\|idle` in the registry (investigation §H) | Rejected: two sources of truth for one fact. After a new SPEC overwrites the idle one, a stale `idle` in the registry would excuse an active SPEC from the contract. The registry keeps only locations. |
| Prose such as "No active /spec" (Radar today) | Rejected: natural language is ambiguous and survives truncation or edits; this is the `UNKNOWN` conflation being removed. |
| Empty or absent `## Milestones` means idle | Rejected: indistinguishable from a truncated or damaged SPEC, which must FAIL. |
| Full YAML frontmatter via a YAML library | Rejected: third-party dependency, and a general parser accepts structure (anchors, multi-document, flow style) that makes "exactly once, exactly this value" harder to guarantee. |
| Tolerant matching (any case, any spacing, quoted values) | Rejected: tolerance is how a typo or truncation could read as inactive. Near-misses FAIL with a diagnostic. |
| Default scope of root plus one directory level (old behaviour) | Rejected: depth was arbitrary and the exclusion list (`docs`, `scripts`, hidden dirs) was a guess. Root by default, anything else registered, nothing skipped silently. |
| `ERROR` as a fifth per-check status | Rejected by the Owner: the approved contract has exactly four. A checker that cannot run is a tool failure (exit 2 plus an `error` object), not a result of any check. |
| `INCOMPLETE` for unassessed dimensions (investigation §H) | Replaced by `assessed: false` with no status: INCOMPLETE would force a non-zero exit on every structural check that, by design, does not run tests or reviews, or else be ignored. A `NOT_EVALUATED` status value was tried and dropped because it is a fifth value in the per-check vocabulary. |

Points where this ADR extends or departs from the investigation's target
design (§H) and the approved plan, for the Owner's review: state in the
SPEC (frontmatter) rather than the registry; frontmatter as a new SPEC.md
element; `assessed: false` for dimensions this version does not assess;
the tool-failure channel (exit 2 plus `error`)
for what the plan lists as "checker could not run" under FAIL or SKIP;
the Milestones section also ending at a level-1 heading; invalid
checkbox markers diagnosed instead of ignored; a SPEC held as a symlink,
submodule or directory failing; an idle SPEC being N/A regardless of its
body; empty `--push-stdin` being a tool failure rather than a pass. The
tool's own source commit, listed in the investigation's result contract,
is not emitted: a copy materialized into a consumer cannot truthfully
name its upstream commit, so provenance is left to the release-integrity
stage's receipts.

## Consequences

- **SPEC.md as a document class no longer supports HTML comments, HTML
  tags, blockquotes, nested lists or lines indented four or more
  columns, anywhere in the document, nor invisible or non-ASCII
  whitespace, nor setext headings, and only column-0 paired fences.**
  This is a deliberate narrowing of the format going forward, not an
  implementation detail: a SPEC using any of them FAILs even where the
  construct is harmless (a `<placeholder>` inside backticks, a
  hanging-indent continuation line, a `> ` note, an emoji sequence with
  a zero-width joiner, a soft hyphen). Inside Milestones, any other
  bullet or numbered list, and any line starting with `[`, `\` or `&`,
  FAILs as well (Decision 5).
- **Expected pre-migration result: today's SPEC files FAIL. This is
  correct behaviour, not a defect.** The numbers below were measured
  on 2026-10-21 with this exact code (the checker's own deny
  definitions, uncapped, on the committed blobs at HEAD of
  article-pipeline `495cad9`, `brain-main-clean-2026-10-21` main
  `88bc18c`, and radar `9adfb09`; the detached `brain` checkout has
  uncommitted edits and gives slightly different counts). The decision
  was taken expecting this cost to be zero; the measurement does not
  confirm that, and the Owner has been told.
  - article-pipeline `SPEC.md` (965 lines): FAIL. 457 lines indented four
    or more columns (hanging-indent continuation lines), 68 nested-list
    items, 2 lines with `<...>` text, 72 lines inside Milestones that are
    not checkbox items, sub-headings or plain text.
  - Brain `SPEC.md` (581 lines): FAIL. 10 nested-list items, 5 lines with
    `<placeholder>` text (inside backticks), 10 numbered items and 15
    other non-checkbox list lines inside Milestones.
  - Brain `verification/SPEC.md` (695 lines): FAIL. 106 blockquote lines,
    21 nested-list items, 6 lines with `<...>` text, 2 indented lines, 6
    numbered items inside Milestones.
  - Radar `SPEC.md` (22 lines): FAIL, not for a denied construct but
    because it is prose-only idle with no frontmatter
    (`MILESTONES_MISSING`).
  - The formatted-heading rule (`DENIED_HEADING_MARKUP`) finds nothing in
    any of the four files.
  Migrating the files, or amending the deny-list (for example to allow
  hanging-indent continuation lines and `<placeholder>` text inside
  backticks), belongs to the Owner and to the consumer stages; nothing
  in this stage changes a consumer.
- Every consumer SPEC that should be able to go idle needs the three
  frontmatter lines. Active SPECs without frontmatter keep working.
- A key spelled `specstate` (no hyphen) is not treated as a near-miss of
  `spec-state`; the declaration is then absent and the SPEC is read as
  active. Accepted as a non-issue by the Owner (the result is a FAIL,
  not a PASS, unless the body is a valid active SPEC).
- A SPEC starting with a thematic break `---` is read as frontmatter and
  FAILs if it is not flat `key: value` or is never closed.
- ToolTempest checked against itself reports SKIP, because
  `tests/fixtures/SPEC.md` is a SPEC file outside the (implicit) scope.
  ToolTempest still has no SPEC canon of its own.
- Adding files under `scripts/` and `schemas/` means `MANIFEST.txt`
  grows by the checker files; consumers see them only when they choose
  to repin.
- The checker verifies structure only. A PASS says nothing about tests,
  documentation impact, review, acceptance, publication or owner
  acceptance.

## Confirmation & Revisit

Confirmed by `python3 -m unittest discover -s tests/docops -t
tests/docops` (217 tests, run on Python 3.14 and 3.9): the four-case
policy, frontmatter state (idle, duplicate, unknown value, near-miss key,
unterminated, non-flat, invisible characters before the delimiter,
truncation at every byte), each deny-list construct in each position,
closed versus unclosed, indented, nested and mismatched fences, a
checkbox inside a closed fence not being counted, working tree vs index
vs arbitrary commit, non-HEAD and multiple pushed refs including a
deleted one, structurally invalid and corrupted SPECs, multiple
registered tracks, CRLF and unreadable encodings, out-of-scope nesting,
the Goals-checkbox false positive, every checker failure mode, schema
conformance and an unchanged-repository check, plus a randomized oracle
that no document holding a denied construct outside a fence may PASS.
Seventy-seven deliberate source mutations were each caught by at least
one test. Each reviewed exploit input was run against the code state
that preceded its fix and failed there, and passes now; for the final
round (formatted headings, the unassessed-dimension schema) all 9 of the
9 new tests fail on the previous state and pass on this one. A closed
fence containing a checkbox passed on the earlier designs as well and
still passes.

Revisit if the Owner amends the deny-list after seeing the consumer cost
above, if a consumer needs per-track state not expressible in the SPEC
itself, if a second inactive-style state appears, if a hook caller needs
the empty-push case to pass, or when the later stage that fills the
unassessed dimensions changes `schema_version`.

## Source

DocOps tooling redesign, Stage 1 (session of 2026-10-21): the approved
Stage 0 plan (unified SPEC policy, criteria B1/B2, marker left to this
stage) and the Codex DocOps investigation (§C, §D, §F, §G, §H, §I). The
frontmatter location, the deny-list rule with its whole-document scope,
the simple column-0 fence form, and the four-status-plus-exit-2 error
contract are Owner decisions given after repeated blocked review rounds
on the earlier designs; the key name `spec-state`, the exact deny-list
tests and the registry split are Claude Code's derivation within that
mandate.
