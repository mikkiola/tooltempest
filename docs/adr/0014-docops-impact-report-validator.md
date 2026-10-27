# ADR-0014: DocOps Impact-Report Validator — DOC_IMPACT_ASSESSMENT From an Externally Supplied, Snapshot-Bound Report

## Status

Proposed, 2026-10-22. Implements Stage 2 of the DocOps tooling redesign
(simplified change-impact review). Becomes Accepted when the Owner
accepts the decisions below. It extends ADR-0013 (also Proposed) in two
places only: Decision 9 there says the six non-structural dimensions are
always `assessed: false`, and this record makes one of them,
`DOC_IMPACT_ASSESSMENT`, assessable when a report is supplied; Decision 10
there reserved evidence handling for this stage, and this record
implements the read-only half of it (validation, no recording). It
supersedes nothing.

## Context & Constraints

**Governing sources, stated explicitly.** The approved requirement source
is the Stage 0 plan (`pasted-content-id-95eb-evidence-and-enchanted-sprout.md`,
section B and criteria table G). Its rows marked Stage 2 are B3, B3a, B3b,
B4 and B5; section B also fixes the statuses (PASS confirmed, FAIL
confirmed failure, SKIP not done with a reason, N/A justified
inapplicability), the five entry modes, proportionality (a small edit does
not need a table of every document category), and the rule that affected
evidence is marked stale and re-checked while the rest is not repeated.
The Owner's Stage 2 instruction of 2026-10-22 adds the interface
(`--impact-file PATH|-`), the two report profiles, base and subject tree
binding, evidence freshness, and this record's status.

Two documents are not approved requirement sources and were used only
where this record says so: the draft contract
(`stage0-contract-draft.md`, labelled a draft) and the Codex
investigation report. Provenance of each rule below:

| Rule | Source |
|---|---|
| Five modes; mode 2 organised by a plan; modes 3 to 5 need neither plan nor SPEC; `/spec` not invoked; report names the mode (B3, B3a, B3b) | approved plan |
| Minimal versus full report (B4); stale evidence re-checked, the rest kept (B5) | approved plan, with the Owner's instruction for the exact shape |
| The eight category names | a Stage 2 design decision, confirmed by the Owner on 2026-10-22 as the working taxonomy; the names were derived from the draft contract and Codex §H, and are **not a requirement of the approved Stage 0 plan**, which does not enumerate categories |
| Exit code 1 for an incomplete required assessment; exit 3 left to Stage 4 | the Owner's instruction of 2026-10-22 (bounded final correction). The approved Stage 0 plan fixes no exit codes (checked by reading it); exit 3 for a SPEC_STRUCTURE SKIP already exists from Stage 1 (ADR-0013) and is not an approved Stage 0 requirement either |
| Evidence record fields (tree, scope, kinds, test counts), outcome-consistency rules, SPEC-created cross-check, path-coverage rule | Claude Code's derivation within the Owner's instruction; not in any earlier document |
| Tests that were not run, zero or partial cannot become PASS; a commit message or a single exit code does not close a criterion | the Owner's required-fixtures list; also Codex Stage 3 text (not approved) |

Constraints that bound the answer: the checker stays read-only and
standard-library only; it writes, stages, commits and publishes nothing;
there is no `record` subcommand and no report generation; no tracked
evidence report is created; the per-check vocabulary stays exactly
PASS, FAIL, SKIP, N/A; semantic truth (that a change is small, that a
category is unaffected, that a document is consistent) is not something a
program can establish, so the validator must not claim it; Stage 1 and its
commits are closed.

## Decision

1. **Input.** `docops check|report ... --impact-file PATH|-` reads one
   JSON report written by whoever reviewed the change. `-` is standard
   input and cannot be combined with `--push-stdin` (both would read it;
   exit 2). A relative path is relative to the working directory. The
   report is UTF-8 JSON of at most 1 MiB with no byte-order mark, no
   duplicate keys and a top-level object; anything else is a FAIL of the
   dimension, never a crash. A report that cannot be read at all (missing
   file, directory, permission) is a tool failure, exit 2, with no
   snapshot evaluated, like an unreadable policy. The input format is
   `schemas/docops-impact-report.schema.json` (`schema_version` 1); rules
   that need more than one field are enforced by the checker and listed in
   the schema's description. Unknown fields are rejected, so a stray
   `closes` or similar field cannot pass silently.
2. **Modes (B3, B3a, B3b).** `mode` is one of `spec_driven`,
   `plan_driven`, `audit_test_driven`, `direct_fix`, `follow_up`. In modes
   2 to 5 a `process` block is required with `spec_skill_invoked` and
   `spec_created` both `false`, so those modes demonstrably run without
   `/spec` and without a new SPEC. Mode 2 additionally requires
   `plan: {reference, used: true}`, the plan artifact that organised the
   work. In every other mode a plan is not required, but an existing one
   may be referenced with the same `plan` block (a follow-up that reuses an
   earlier plan is legitimate), and an existing SPEC needs no mention at
   all. Contradictions still fail: `spec_skill_invoked` or `spec_created`
   true in modes 2 to 5, and `plan_created` true in modes 3 to 5
   (referencing a plan is not creating one). The checker also compares the
   report with the change (the SPEC-created rule, below). It cannot see whether `/spec`
   was invoked or whether the plan exists; those are statements.
   **Mode 1 (SPEC-driven) keeps no structural requirement beyond the common
   baseline, as a reasoned decision.** Its SPEC is already independently
   validated by the checker's tree-reading and SPEC check (B1, B2) in the
   same run; requiring the impact report to identify or validate it again
   would duplicate that coverage and close no gap.
   **The SPEC-created rule is a decision with rationale, not an open
   limitation.** It runs whenever the report states `spec_created: false`:
   modes 2 to 5 must state it, and a mode 1 report may. It is deterministic
   and uses path and registration only. Candidate paths are the union of the
   SPEC track paths registered in the base policy and in the subject policy
   (each including the implicit root `SPEC.md` track when that snapshot has
   no policy file). A candidate that has a file in the subject tree is
   *created* if it was not registered as a track in the base policy, or if
   no file existed at that path in the base tree. Any created candidate
   makes the report FAIL (`SPEC_CREATED_CONTRADICTS_REPORT`). A candidate is
   classified the same way whether its file was newly written, previously
   existed as an unregistered file, or was relocated from another path.
   Because the base's tracks are candidates too, removing a registration
   while a file appears at that path in the same change is still detected.
   A SPEC that stays at its already registered path, edited, is not
   created. Files that merely have the name `spec.md` outside the
   registered tracks are Stage 1's out-of-scope warning, not candidates.
   No correlation between removed and added paths is attempted.
   Move-detection was tried and is removed: three consecutive review rounds
   each found a new bypass of the same class (a non-SPEC file renamed to a
   tracked path with identical bytes, a note registered as a SPEC through
   the policy file, and an unregistration combined with creation in one
   change). As with the Markdown deny-list in ADR-0013, the resolution is
   architectural, not another patch: a rule that this kind of change can
   no longer defeat, at the accepted cost that recognising a move is lost.
   The cost, generically: a future consumer migration that relocates a
   SPEC path will be treated as creation at the destination path. The
   impact report must acknowledge the creation truthfully
   (`spec_created: true`) and represent the applicable mode and process;
   modes 2 to 5 cannot say `true` (a created SPEC contradicts those modes),
   so the truthful report is a mode 1 one. Selecting `mode: spec_driven`
   alone does not override a contradictory `spec_created: false`. If the
   base policy cannot be read or parsed, nothing is substituted for it, not
   an empty policy and not a guess: which paths were SPEC tracks is then
   unknown, so the assessment stops with `SPEC_POLICY_UNREADABLE`, its
   dimension is SKIP (incomplete, exit 1 under Decision 8), and it is never
   PASS. The base policy is read only when the report states
   `spec_created: false`. The two policies fail differently because their
   roles differ. The subject policy underlies the core SPEC structural
   check for the whole snapshot, so an invalid subject policy is a tool
   error (exit 2, Stage 1's existing `POLICY_INVALID`), not an incomplete
   impact assessment. The base policy is read only for this one created-SPEC
   check, so its failure is scoped to that check's incompleteness (SKIP,
   exit 1, `SPEC_POLICY_UNREADABLE`). A Git failure while reading the base
   policy blob is the existing tool-error class (`IMPACT_GIT_ERROR`, exit 2).
3. **Profiles (B4).** `profile: minimal` carries `minimal:
   {affected_paths, reason, evidence}`: the paths the change affects and the
   reason a full category review is not required. `profile: full` carries
   all eight categories, each with exactly one of `affected_paths` or
   `no_impact_reason`, plus the evidence it rests on. The eight are
   `architecture`, `interfaces`, `failure_behaviour`, `configuration`,
   `tests`, `decisions`, `acceptance`, `planning_and_operations`
   (architecture and responsibilities; interfaces, schemas, algorithms;
   error handling and behaviour on failure; configuration, installation,
   dependencies; tests and guarantees; ADRs and decisions; SPEC and
   acceptance criteria; backlog, mission, roadmap, operational
   documents). The checker verifies that the chosen profile has its
   fields. It does not decide, from the size of the change or the text of
   the reason, whether the change deserves the minimal profile: a large
   change reported as minimal passes if the report is complete.
4. **Identity.** `subject_tree` must equal the tree of the snapshot under
   check. For a commit that is the commit's tree. For the index it is the
   tree `git write-tree` would give, computed in memory by
   `gitstate.index_tree_oid` because `write-tree` adds objects to the
   repository; an index with unmerged entries has no tree and the report
   FAILs. `base_tree` must be a tree object present in this repository
   (absent: SKIP, because the change cannot be established; a commit or
   blob: FAIL). Base and subject must differ: with equal trees there is
   no change for the report to describe, and the format has no
   representation of a no-op (a report names at least one affected path),
   so a report claiming changes over an empty comparison is a FAIL
   (`REPORT_BASE_EQUALS_SUBJECT`) rather than a vacuous PASS. When the invocation supplies
   the old tip of a pushed ref (`--push-stdin`), the report's base must
   equal that tip's tree (mismatch: FAIL; tip not in this repository: SKIP)
   and the result says `independently_verified: true`. Otherwise, including
   a new ref and every `--tree` run, the base is recorded as reported and
   the result says it was **not** independently verified, in the reason and
   in a `note` diagnostic and in the text report; no option to supply a base
   was added. A PASS is still possible in that case (otherwise `check
   --tree` could never succeed), but it is explicitly a PASS relative to
   the reported base and does not claim the assessment is fully verified. If the subject does not match,
   nothing further is judged against that snapshot.
5. **Completeness.** The changed paths are computed between the base tree
   and the subject, path by path, comparing mode and object id. Every
   changed path must be covered by the union of the report's
   `affected_paths` (a path, or a directory by prefix); otherwise the
   result is SKIP with the first uncovered paths named. Every cited
   evidence id must exist. Each portion's evidence scopes must cover that
   portion's affected paths, and a `no_impact_reason` must be backed by
   evidence whose scopes cover the whole change: nobody can claim no impact
   over paths nobody examined.
6. **Freshness (B5).** Each evidence entry names the `tree` it was gathered
   on and its `scope_paths` (paths, directories, or `.` for the whole
   tree). It is *fresh* if its tree is the subject, or if no path inside
   its scope differs between its tree and the subject (added, deleted,
   content or mode change). Staleness is measured from the evidence's own
   tree to the subject, which is the base-to-subject change when the
   evidence was gathered on the base; completeness (Decision 5) is measured
   from the reported base. It is *stale* if one does. It is
   *unverifiable* if its tree is not in this repository, and *invalid* if
   that object is not a tree. A portion is as good as its worst cited
   evidence. A stale or unverifiable portion makes the dimension SKIP with
   "renewed review required"; portions whose evidence is still fresh stay
   PASS in the result, so only the affected part needs redoing. The
   dimension is never PASS while any required portion is stale. What this
   does not establish: a change outside every evidence scope never makes
   anything stale, so an honest scope is the reviewer's responsibility; a
   broad scope (`.`) is conservative, since any change then invalidates
   it.
7. **Evidence kinds and outcomes.** `kind` is `test`, `review` or
   `inspection`; a commit message, a trailer or a bare exit code is not an
   evidence kind. A `test` entry needs `command` and `tests: {run, passed,
   failed, skipped}` (an exit code alone is not accepted). Outcome `pass`
   needs `run > 0`, every test passed, none failed or skipped, and exit
   code 0 when given; `fail` needs a failed test or non-zero exit code;
   counts must add up. A mismatch is a FAIL of the report as inconsistent.
   Fresh evidence recording a failure FAILs its portion; `partial` and
   `skipped` evidence leaves the portion SKIP.
8. **Result.** `checks.DOC_IMPACT_ASSESSMENT` stays `{assessed: false,
   reason}` unless a report is supplied. With one it is `{assessed: true,
   status, reason, validated, semantic_review, report, mode, profile,
   subject, base, portions, diagnostics}`. `status` is PASS, FAIL or SKIP
   and never N/A. `validated` always lists exactly `structure, identity,
   completeness, freshness`, and `semantic_review` is always
   `"not established"`: a PASS says the review is recorded, bound to this
   snapshot, complete against the change and current, never that the
   documentation is right. Each diagnostic carries an `effect`: `fail`,
   `skip` or `note`. The snapshot status is the worst of
   `SPEC_STRUCTURE` and this dimension. A malformed or inconsistent report
   is FAIL (exit 1); stale, incomplete or unverifiable is SKIP, and a SKIP
   of this dimension also exits 1, because a required assessment that is
   not complete is not satisfied: the per-check status stays SKIP, the
   command never exits 0, and no new exit code is introduced. Exit 2 is
   reserved for tool, usage and I/O errors: an unreadable report file or a
   Git failure while validating is a tool failure (exit 2, `error`
   object), never a verdict. Exit 3 keeps the meaning Stage 1 gave it
   (SPEC applicability not established) and is not used for this
   dimension; a dedicated code for incomplete assessments is deferred to
   Stage 4. A snapshot's and the summary's exit code is the worst of the
   parts: 1 over 3 over 0. For a deleted ref
   and a well-formed report the dimension stays `assessed: false` with the
   reason that no tree exists; a malformed report is still a FAIL there,
   since structure does not depend on a subject.
9. **Versioning.** `RESULT_SCHEMA_VERSION` stays 1 because a run without
   `--impact-file` emits the same document as before, and no consumer has
   adopted the v1 schema; the schema file is extended in place. The tool
   version becomes 0.2.0. The input report has its own `schema_version` 1.
10. **Out of scope, kept out.** No `record` subcommand, no report
    generation, no scoring, no interpretation of free text, no persistent
    tracked report file, no network, no model calls.

## Alternatives & Rationale

| Option | Verdict |
|---|---|
| (a) Rules text only; the "common report" is the task's final report | Rejected by the Owner (Q1 = b). B3 to B5 would stay untestable. |
| (b) This record: validate an externally written report | Chosen. Only option that makes the plan's fixture scenarios runnable while leaving judgment to the reviewer. |
| Generate the report from the diff | Rejected. Choosing categories, paths and reasons is the semantic judgment the plan keeps with the reviewer. |
| Persist reports (tracked file, git notes, refs) | Rejected. The plan allows no permanent artifact unless an existing workflow needs one, and notes or refs would make the checker write. |
| Decide minimal versus full from change size or paths | Rejected by the Owner. A size threshold would be an arbitrary rule and gameable. |
| Decide whether a stated reason or `no_impact_reason` is plausible | Rejected: not decidable; a PASS would then overclaim. |
| A fifth status such as INCOMPLETE or STALE | Rejected. The vocabulary is fixed at four; SKIP with a reason is the plan's "not done or not doable". |
| Treat a stale portion as FAIL | Rejected. Stale means not re-verified, not wrong. FAIL is kept for confirmed inconsistency or recorded failure. |
| Per-evidence blob hashes for staleness | Rejected as heavier than needed. Path-level difference between two trees, scoped by the evidence's own paths, answers "did what it covered change". |
| Invalidate all evidence on any change | Rejected. It contradicts B5 ("the rest is not repeated"). |
| `git write-tree` to learn the index tree | Rejected. It adds objects to the repository; the checker is read-only. In-memory hashing is tested against `write-tree` for ordering, modes, symlinks, gitlinks, unicode paths, empty and sha256 indexes; an intent-to-add entry is deliberately not matched (see Consequences). |
| Add `--base REV` | Deferred. The Owner said to use a supplied base only where the existing invocation provides one. |
| Bump `RESULT_SCHEMA_VERSION` to 2 | Rejected for now, see Decision 9. Revisit if a consumer pins the v1 schema before this record is accepted. |

## Consequences

- The checker can now say, for a supplied report: well-formed, for this
  snapshot, complete against the actual change, and current. It still says
  nothing about tests (`TESTS` stays unassessed; test counts inside a
  report are claims about evidence, not a run by the checker),
  documentation consistency, acceptance, publication or owner acceptance.
- A report is a claim. The checker cannot tell that `/spec` was not
  invoked, that a plan exists, that a reason is true, or that an evidence
  scope is honest. A complete but false report passes; a test pins this so
  the limit stays visible.
- Using the index as the subject needs the reviewer to have run
  `git write-tree` (or to commit) so the evidence trees exist; evidence on
  a tree that has since been garbage-collected is unverifiable and gives
  SKIP, not PASS. An index containing an intent-to-add entry computes a
  tree that differs from `write-tree`'s and fails closed as a subject
  mismatch.
- The path-coverage rule means a report must list every changed path, code
  files included, under `affected_paths` of some category. That is more
  than "affected documents" and is the price of tying the base to the
  report: otherwise the base would be decoration.
- Completeness is relative to the reported base. Without a supplied old tip
  a base chosen to make the change look small makes the check weaker; the
  result says so in its reason and in the text report, and only a pushed
  ref's old tip verifies it. The extreme case, a base equal to the
  subject, is rejected outright.
- Incomplete required assessments exit 1, the same code as a violation, and
  are told apart by the per-check status (SKIP versus FAIL) and the
  diagnostics, not by the exit code. Exit 3 remains the Stage 1 SPEC code.
- Only a registered SPEC track, in the base or the subject policy, can be
  found created by a change. A SPEC at a path neither policy registers is
  invisible to this rule (Stage 1 reports it as an out-of-scope warning). A
  relocated SPEC counts as created at its destination; there is no
  move-recognition (Decision 2).
- Evidence that records a failure on a current tree FAILs the dimension even
  if no portion cites it. Other uncited evidence is only noted, except that
  uncited evidence on a tree missing from the repository still makes the
  result SKIP, since its freshness cannot be established. A cap of
  100 diagnostics never changes the verdict: the overflow note carries the
  most severe effect it hides.
- A run without a supplied old tip cannot call the base verified. A
  pre-push hook that passes the push lines gets verification; `check
  --tree` does not.
- Without `--impact-file` the verdicts and exit codes are unchanged; the
  not-assessed reason text and the tool version changed (Decision 9). The consumers'
  current SPEC files still FAIL the Stage 1 deny-list; this record neither
  changes that nor needs the consumers to migrate.
- `MANIFEST.txt` gains the input schema and `impact.py`; `README.md` and
  `CLAUDE.md` are updated in the same change (the README's composition
  count is reconciled with the manifest; `CLAUDE.md` names the DocOps test
  suite).

## Confirmation & Revisit

The figures below are the author's runs of 2026-10-22; the mutation log is
not kept in the repository and the Owner or a reviewer can re-run both
commands. Confirmed by `python3 -B -m unittest discover -s tests/docops -t
tests/docops`: 340 tests (the 221 of ADR-0013, one of which was updated
because it pinned the old schema, plus 119 new in `tests/docops/test_impact.py`),
all passing on Python 3.14.5 and 3.9.6. What each group establishes:

| Criterion | Tests | Proven | Not proven |
|---|---|---|---|
| B3, B3a, B3b | `ModeTests`, and the mode rows of `StructureTests` | each of the five modes is accepted with its fields and named in the result; mode 2 needs a plan block with `used: true`; modes 2 to 5 need `/spec` not invoked and no SPEC or (modes 3 to 5) plan created, and reject contradictions; an existing plan may be referenced in any mode; a SPEC track that is newly registered or whose file is newly present (including a note renamed or registered as a SPEC, a relocated SPEC, and a registration removed while its file appears) contradicts a report stating `spec_created: false` in any mode, while a SPEC edited at its registered path and a merely similarly named file do not; an unreadable base policy stops the assessment as SKIP, never PASS | that `/spec` was not invoked, that the plan exists, that it organised the work |
| B4 | `ProfileTests`, the profile rows of `StructureTests` | a minimal and a full report each need exactly their own fields; all eight categories are required in a full report, each with paths xor a reason; a 32-path change reported as minimal passes when complete; equal base and subject is a FAIL | whether a change truly is minimal; whether any reason is true |
| B5 | `FreshnessTests` | evidence goes stale when a path in its scope was added, deleted, edited or its mode changed, and not for other paths or a sibling prefix; two evidence entries over different scopes leave one portion SKIP and the other PASS; the dimension is SKIP while any required portion is stale and PASS again with renewed evidence; unverifiable or non-tree evidence trees never read as fresh | that a change outside every evidence scope is irrelevant |
| Identity | `IdentityTests`, `Sha256RepositoryTests`, `TreeHashTests` | subject must equal the snapshot tree (commit, index, sha256 repository); the in-memory index tree equals `git write-tree` for ordering, modes, symlinks, gitlinks, unicode and empty indexes and is never written; absent, wrong-type and mismatched bases; push old tip verifies a base, a new ref or `--tree` does not claim it, and says so | that a base without a supplied old tip is the intended one |
| Result contract | `ExitCodeTests`, `ResultContractTests`, `StructureTests` | PASS exits 0, FAIL and SKIP of this dimension exit 1 (an incomplete assessment never exits 0, and is distinct from a tool error, exit 2), Stage 1's SPEC SKIP still exits 3; results conform to the result schema; never N/A; an unreadable report or a Git failure is exit 2; an invalid subject policy is Stage 1's `POLICY_INVALID` (exit 2) and a Git failure reading the base policy is `IMPACT_GIT_ERROR` (exit 2), unlike an unreadable base policy (SKIP, exit 1); nothing in the repository or the report file changes; no `record` subcommand; a complete but false report passes and says `semantic_review: not established` | any semantic property |

The input schema and the checker are cross-checked on 125 structural cases
(104 malformed and 21 well-formed reports, accepted or rejected the same way
by both) and a further 12 malformed reports whose defect needs more than one
field and is enforced by the checker alone; the path rules are cross-checked
on 25 samples.

Mutation testing: 232 deliberate source mutations (151 on the new and
changed code, 81 carried over from ADR-0013) were each caught by at least
one test in the final run. In an earlier run on the new code, 10 of 118
survived; each showed a test gap (an over-long evidence id, unknown fields
in a category, negative counts, a string exit code, an index tree that does
not exist as an object, a failure downgraded by other incomplete evidence,
a sha256 repository, an invalid evidence tree not failing its portion) and
was closed with a test, then caught. Run against the code of ADR-0013 the new test
module fails (the validator does not exist) and the updated schema test
fails; the remaining 220 tests pass on both. After the bounded correction of
2026-10-22 (exit codes, base equal to subject, mode restrictions) the
corrected tests were run against the code that preceded it: 20 test methods
(29 failing items) fail there and pass now; the tests written for the final SPEC-created
rule (a relocated SPEC, an acknowledged relocation, a registration removed while
its file appears, the implicit root track with an empty registry, an unreadable
base policy) were run against the code that preceded it: 5 test methods (12
failing items) fail there and pass now.

Revisit if the Owner changes the category list, if a dedicated exit code for
incomplete assessments is introduced (deferred to Stage 4), if a consumer needs a base option on `check --tree`, if a
consumer pins the v1 result schema before acceptance (then bump it), if
evidence staleness by scope proves too coarse in practice (for example,
reviewers routinely use `.`), or if a later stage records reports and the
no-write rule changes.

## Source

DocOps tooling redesign, Stage 2 (session of 2026-10-22). Requirements:
the approved Stage 0 plan (B3, B3a, B3b, B4, B5, section B) and the
Owner's Stage 2 instruction (decisions Q1 = minimal read-only validator,
Q2 = fix the two documentation findings in the same pass). Owner
decisions: the interface, the two profiles, base and subject binding,
freshness semantics, ADR status. Derived by Claude Code within that
mandate: the report fields and evidence record, the coverage and
SPEC-created cross-checks, the status mapping, the in-memory index tree.
Not approved sources, used only as flagged above: the draft contract and
the Codex report.
