# ADR-0020: One Universal Rules File, Delivered in the Contract Root

Status: Accepted by the Owner, 2026-11-10.

## Context & Constraints

`docs/reference/documentation-rules.md` held the rules for documents and process for all projects. Observation at commit `1dc54c3`: `git show 1dc54c3:docs/reference/documentation-rules.md | wc -l` prints 822, and the materializer never installs the file: `git show 1dc54c3:scripts/tooling_core/declarations.py | grep -n "consulted in place"` prints the line where `parse_manifest` reports every `docs/reference/` path as "consulted in place, not copied". A consumer therefore had no pinned copy of the rules.

Observation, from the Owner-requested audit of 2026-11-10 (not reproduced here): `docs/CONSTITUTION.md` of `article-pipeline` at `feae5c0`, of `brain` at `896e911` and of `radar` at `7e66e19` each restate these rules in their own words, and the texts differ. Command: `git show <commit>:docs/CONSTITUTION.md` in each repository.

ADR-0017 defines the contract component as one consumer-facing file in its own root, `docs/tooltempest/`, installed by the consumer target. The same function installs any manifest path that starts with `docs/tooltempest/`, and the receipt schema accepts any path under that root. `tests/distribution/test_contract.py` asserted that the root holds exactly one file (`git show 1dc54c3:tests/distribution/test_contract.py | grep -n "one file in its own root"`).

Constraint, stated by the Owner on 2026-11-10 and not reproducible from any repository: the workspace instructions outside the repositories name `docs/reference/documentation-rules.md` as a file to read in full, and a global hook checks that read. That path cannot disappear.

The Owner set the text rules for every document on 2026-11-10: Docs as Code, one document type per document (Diátaxis), structured writing, one source per fact (single sourcing), obligations in the key words of RFC 2119, the shortest text that keeps the meaning, English in Google developer documentation style.

## Decision

The universal rules for documents and process live in one file, `docs/tooltempest/universal-rules.md`. It is the second file of the contract root, listed in `MANIFEST.txt`, and delivered to every consumer by the existing materializer. No mechanism is added.

The contract component is every file under `docs/tooltempest/`. An Owner decision to change any of them authorizes the repin itself, the checks, and a local commit of the result; it does not authorize publication (Owner decision of 2026-11-11).

The file opens with the section "How every repository works". Entry points and Constitutions link to that section and do not copy it. A Constitution holds project-specific rules only.

The rules are binding. A document that contradicts a rule is defective and is fixed; the rule is changed only by an Accepted ADR in this repository.

`docs/reference/documentation-rules.md` stays at its path as a short instruction to read the new file in full, because the workspace `CLAUDE.md` and the global hook name that path. Both change in the closing phase of this work, after the ToolTempest push, when the new file exists on `main`. This change edits neither.

This record supersedes ADR-0017 in one point: the contract as one file. ADR-0017 stays Accepted for every other point and is not edited. ADR-0008 is not edited.

## Alternatives & Rationale

- Rewrite `docs/reference/documentation-rules.md` in place: rejected, because the materializer does not install that root (Context), so consumers would still hold no pinned copy.
- Install `docs/reference/` as well: rejected, because it changes the materializer and the meaning ADR-0008 gave that root.
- Put the rules inside `consumer-contract.md`: rejected, because that file states one obligation and its repin evidence, and a second subject would mix two documents in one.
- Keep the rules in each Constitution: rejected, because the copies differ (the audit observation above).
- Delete the reference document: rejected, because a required-reading path outside this repository names it.
- Split the file by document type (reference, how-to): rejected. The file is a reference. A split gives two delivered files and breaks the one-file point of ADR-0017.

## Consequences

- A consumer has the rules only after it repins and materializes (Decision, first paragraph).
- The convention of dated notes inside a Constitution or a PROJECT document ends. Git history and ADRs hold the history.
- A Constitution no longer follows the sixteen-item structure of the earlier reference document.
- The manifest holds two paths under `docs/tooltempest/`, and the contract test asserts both (`grep -n "assertEqual(in_root" tests/distribution/test_contract.py`).
- The payload paths of the repin evidence are unchanged; the contract root is not payload.
- This record exceeds the size that DR-8 suggests, because the Owner's invariant of 2026-11-10 requires every deletion and change of meaning to be recorded with a command (DR-8 allows a stated reason). Content of the earlier reference document that is deleted, not carried over (DR-9). In each command, `OLD` is `git show 1dc54c3:docs/reference/documentation-rules.md`.
  - Item 10, the response format by task type: `OLD | grep -n -i "response format by task type"`. No universal rule replaces it. The other three repositories keep their own response-format tables: `git show feae5c0:docs/CONSTITUTION.md | grep -n "Response format by task type"` in `article-pipeline`, the same with `896e911` in `brain` and `7e66e19` in `radar`.
  - Item 12, the example model and login restriction for `/spec`: `OLD | grep -n "Sonnet only"`. The restriction lives in `skills/spec/SKILL.md`, lines 11 to 18, which this change does not touch.
  - The sixteen-item structure of a Constitution: `OLD | sed -n 56,260p | grep -n "^ *[0-9]*\. \*\*"`. Its universal content moved into "How every repository works" and WR-1 to WR-14; the role item belongs in each repository's Constitution.
  - The statement that spellings such as `SPEC.txt` are not detected: `OLD | grep -n "SPEC.txt"`. `README.md` states it.
  - Guidance that is deleted deliberately, although some of it reads as an imperative: a Constitution gains a rule when a recurring failure is found (`OLD | grep -n "recurring failure mode"`); the ADR index is exempt from the filename and numbering rules (`OLD | grep -n "exempt from the numbering"`); an index generator is justified only at scale (`OLD | grep -n "premature engineering"`); the roadmap pointer carries no advisory sentence in a table cell (`OLD | grep -n "advisory"`); the roadmap stays short (`OLD | grep -n "keep it short"`); the Source line counts the external cross-checks and says whether they were unanimous (`OLD | grep -n "unanimous"`); the exception to Immutable Lineage for a construction-phase migration (`OLD | grep -n "doesn't apply"`); the ADR `Status` line with date or commit (`OLD | grep -n "date/commit"`); the one-line description of each ADR section (`OLD | grep -n "What actually changes"`).
  - The dated notes and the append-mostly convention for a Constitution or a PROJECT document: `OLD | grep -n -i "dated note"`. The two dated notes of `docs/tooltempest/consumer-contract.md` go the same way: `git show 1dc54c3:docs/tooltempest/consumer-contract.md | grep -n "Dated note"`. Git history and ADRs hold the history.
  - The chain of authority, a ranked list of the document types: `OLD | grep -n "chain of authority"`. The conflict table in "Where the rules live" replaces it.
- Changes of meaning:
  - The scope stays "every project in this ecosystem" (`OLD | sed -n 3,4p`). The delivery is by pin, so a repository without `.tooltempest.lock` holds no copy (the first Consequence).
  - The metadata fields of an ADR (`id`, `status`, `supersedes`, `superseded_by`, `source_type`: `OLD | grep -n "source_type"`) are not listed. DR-7 takes them from the latest Accepted ADR of the repository, as the earlier text already allowed for ToolTempest (`OLD | grep -n "per-project formatting choice"`).
  - The consumer contract no longer says that a project copies it "verbatim (or near-verbatim)" (`OLD | grep -n "near-verbatim"`). It says that a project links to it. Its trigger list gains `docs/reference/` and `docs/tooltempest/`, the six directories that `scripts/check_manifest.py` compares, and its payload definition states that the contract root is not payload. Source: ADR-0017 and `scripts/check_manifest.py`.
  - LG-1 has one exception, `../SYSTEM_GOALS.md`. The earlier text said "no exceptions" (`OLD | grep -n "no exceptions"`) and also said that the registry is in Russian (`OLD | grep -n "written in Russian"`).
  - An index without a generator is updated by hand, and the missing generator is a BACKLOG entry (DR-12). The earlier text called the index a build artifact that is never hand-edited (`OLD | grep -n "build artifact"`; `OLD | grep -n "never edited by"`; `OLD | grep -n "hand-patch"`). Observation: `brain` keeps its index by hand (`git show 896e911:docs/adr/ADR-INDEX.md | head -4` in `brain`). ToolTempest holds no `ADR-INDEX.md` (`git ls-tree --name-only 1dc54c3 docs/adr/ | grep -c INDEX` prints 0), and a repository skips a file it does not hold.
  - Where a current mission governs, the rule applies when `CURRENT_MISSION.md` is `ACTIVE`. The earlier text said "Where adopted" without the condition (`OLD | grep -n "Where adopted"`). The consumer text that states `ACTIVE`: `git show feae5c0:docs/CONSTITUTION.md | grep -n "exists and is"` in `article-pipeline`.
  - The registry `SYSTEM_GOALS.md` is named without a path. The earlier text gave the workspace path (`OLD | grep -n "SYSTEM_GOALS.md"`).
  - The note on the Analyzer inconsistency goes with the dated notes: `OLD | grep -n "Analyzer"`.
  - Decision of the Owner, 2026-11-10: item 9 of the earlier reference document (`OLD | grep -n "Write/Delete/Move confirmation"`, lines 121-123: "every such action anywhere requires the explicit format ... and waits for the answer") is replaced by WR-13. The task prompt is the approval, and inside the task's scope the agent writes, edits, deletes, and moves without a per-action question. This loosens the earlier "every action anywhere" rule. The earlier exceptions for direct-write documents and an autonomous ADR are no longer needed (`OLD | sed -n 81,86p`, `OLD | grep -n "no confirmation gate"` prints lines 390, 455, and 736).
  - Decisions of the Owner, 2026-11-11. Repin: a task prompt that names a repin authorizes the change of `.tooltempest.lock` and its local commit when the change is limited to the repin, the consumer's checks pass, and the evidence shows no change of the consumer's behavior, with what each piece proves and does not prove; the agent stops and asks when the evidence is insufficient, the behavior changed, the lock shows unrelated changes, or the repin carries a contract or client change the Owner has not decided; the autonomous-repin decision of 2026-10-30 stays, and `docs/tooltempest/consumer-contract.md` and WR-13 say the same. Commit and push: a local commit needs no Owner confirmation (the agent checks the full diff and runs the prescribed checks first, and a commit with MEDIUM or LOW findings is allowed when the report lists them, a BLOCKER or HIGH finding blocks a commit under RV-7, and a review gate that the Owner requires before a commit still applies; a blocking hook still blocks); any `git push` needs a separate explicit permission under WR-14, requested in Russian after the mandatory checks pass; older rules that require confirmation before a local commit are void. The `[ЗАПРОС]` question stays for token revocation, deleting a file another process may depend on, and an edit of a Constitution.
  - The sentence that the citation rule is enforced by a script in a pre-push hook is deleted: `OLD | grep -n "enforced mechanically"`. Each Constitution names its own check.
  - DR-9 no longer allows the log file outside the ADR or the tool-version label for a snapshot: `OLD | grep -n "log file outside"`; `OLD | grep -n "tool version"`. The commit message and the command-and-commit label remain.
  - TX-6 and DR-7: the ADR sections and the `Found` and `Source` lines of BACKLOG entries are exceptions to "no history or motivation".
  - DR-6 lets a new ADR supersede one point of an Accepted ADR and leaves the older ADR unedited, `superseded_by` included; DR-5 sets `superseded_by` only for a full supersession. The earlier DR-6 text said that DR-5 sets the field also for a partial supersession. This record uses DR-6; the Owner's decision of 2026-11-10.
  - An imperative sentence without a key word is a MUST ("Key words"), so every imperative of the earlier text binds as a MUST, except the deleted items above and the changes of meaning listed here.
  - DR-2 requires a check of `origin/main`, local branches, and worktrees before an ADR number is assigned (`OLD | grep -n "numbering"` shows the earlier, narrower text).
  - The mission card rules MC-1 to MC-6 are universal and bind a repository that holds `CURRENT_MISSION.md`. The earlier text left the full set to each adopting Constitution (`OLD | grep -n "adopting"`).
  - The earlier text called the absence of `CHANGELOG.md` a default for new projects and said "remove/don't create" (`OLD | grep -n "Default for new projects"`). The universal rule states the default as SHOULD NOT create; removal of an existing file follows the read-and-flag step.
  - BL-1 accepts the ID on a bullet line as well as on a heading line (`OLD | grep -n "inline on the heading line"`). The three consumer backlogs use both forms (Source).
  - A Constitution is no longer changed only "by explicit owner-directed change" (`OLD | grep -n "owner-directed change"`). DR-10 and the Constitution amendment through an Accepted ADR replace it. The earlier text already let the agent write an ADR about the Constitution (`OLD | grep -n "even to decisions about"`).

- The maintainer entry point `CLAUDE.md` loses the rule sentences that a universal rule states: "run it after changes to composition paths" (consumer contract), "reuse prior results" (RV-5), and "keep implementation preparation separate from permission to commit or publish" (WR-13 and WR-14); the base text is `git show 1dc54c3:CLAUDE.md`. Its project-specific rules (the manifest, the `skills/` and `rules/` boundary, no client-specific directory, no repin, install or activation for consumers as a side effect of a source change, no change of consumer locks or hooks during instruction work, no live model-backed invocation) stay in place, because this repository holds no Constitution and the universal rules give no other home; that is an open conflict with the rule that an entry point holds no rule.

## Confirmation & Revisit

The contract test materializes both files of the contract root into a disposable consumer and compares their bytes with the commit.

Revisit when a rule is needed in one repository only and cannot be stated in that repository's Constitution, or when the file grows beyond what one cold-start read can hold.

## Source

The Owner's instructions to Claude Code of 2026-11-10, which approved this change and its direction. No external AI cross-check was made. Sources of rules that the earlier text did not state, or stated differently, by commit and line:
- MC-1 to MC-6: `git show feae5c0:docs/CONSTITUTION.md | sed -n 47,84p`; `git show 896e911:docs/CONSTITUTION.md | sed -n 604,640p`; `git show 7e66e19:docs/CONSTITUTION.md | sed -n 387,425p`.
- WR-2, last sentence: `feae5c0` line 218, `7e66e19` line 94. WR-3: `896e911` lines 85 and 340, `7e66e19` line 278. WR-10: `feae5c0` line 301, `7e66e19` line 154. CT-2: `feae5c0` lines 160 to 190. RV-1, the "without output" sentence: `feae5c0` lines 384 to 387.
- BL-1, bullet line: the BACKLOG formats at the three consumer bases, `git grep -n "\[B-001\]" <commit> -- docs/BACKLOG.md`.
- DR-2 check of 2026-11-10, repository state at `1dc54c3`: `git ls-tree --name-only origin/main docs/adr/ | tail -1` prints 0018; the same command over every ref of `git for-each-ref refs/heads refs/remotes` prints no number above 0018; `ls docs/adr` in each path of `git worktree list --porcelain` prints no number above 0018 except the primary checkout, whose untracked `0019-retiring-a-path-of-an-installed-root.md` `git status --short` lists. This record takes 0020. Other windows are not checked.
- Reading order with `CLAUDE.md` first: the entry points; `CURRENT_MISSION.md` last: `feae5c0` lines 33 to 36. WR-8, the note of what was checked: `feae5c0` lines 289 to 293.
- The precedence rows, "ADR acceptance", the MUST rule for imperatives, DR-2 (every worktree), and DR-6: the Owner's list of 2026-11-10 for the opening section.
- DR-9, a command that prints removed text is evidence: the Owner's invariant of 2026-11-10 that a removed obligation is recorded with a command that prints the old text.
- RV-7: the Owner's task prompt of 2026-11-11 (the HIGH chain, the effect of the severity grades on a commit, the re-review limit, and the budget of two passes per task). It narrows the global rule to run `code-reviewer` again after any post-review change; the global instructions file is not edited here. This record is unpushed, and the Owner instructed that it is editable.
- WR-13 and WR-14: the Owner's decision of 2026-11-10 on write confirmation, given in the task prompt of that date, and the Owner's decisions of 2026-11-11 on repin, commit, and push.
- DR-8 and the Owner's text rules (TX-1 to TX-6, the reading-order fallback, "How a rule changes"): the Owner's instructions of 2026-11-10 and of 2026-11-04 on ADR size.
