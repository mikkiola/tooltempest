# Universal Rules

| Field | Value |
|---|---|
| Type | Reference |
| Applies to | Every repository in the ecosystem. A repository receives this file by pinning ToolTempest in `.tooltempest.lock`. |
| Source | `docs/tooltempest/universal-rules.md` in `mikkiola/tooltempest` |
| Delivery | `scripts/tooling.py materialize --apply` installs the pinned version at the same path. The installed copy is managed. `tests/distribution/test_contract.py` materializes it into a consumer that has no ignore rule for it. |

## How every repository works

### Reading order

At the start of a session, read these files in order. Then state the session
plan. The function of each file is in "Canonical documents".

| Step | File |
|---|---|
| 1 | `CLAUDE.md` |
| 2 | This section |
| 3 | `docs/CONSTITUTION.md` |
| 4 | `docs/PROJECT.md` |
| 5 | `docs/ARCHITECTURE.md` |
| 6 | `docs/ROADMAP.md` |
| 7 | `docs/BACKLOG.md` |
| 8 | `CURRENT_MISSION.md` |

- If this file is absent in a fresh clone, install the pinned files first. The
  repository's entry point names the command.
- Skip a file that the repository does not hold. Where steps 3 to 8 are all
  absent, read `README.md` and `docs/adr/` instead.

### Where the rules live

| Rules | File | Scope |
|---|---|---|
| Universal rules | This file | Every repository |
| ToolTempest consumer obligation and repin evidence | `docs/tooltempest/consumer-contract.md` | Every consumer |
| Project-specific rules | `docs/CONSTITUTION.md` | One repository |
| Decisions | `docs/adr/` | One repository |

On a conflict, apply the first row that matches.

| Conflict | File that wins |
|---|---|
| A universal rule against any repository document | This file and the consumer contract |
| What exists now | `docs/ARCHITECTURE.md` |
| Why a decision was made | `docs/adr/` |
| The current target, when `CURRENT_MISSION.md` is `ACTIVE` | `CURRENT_MISSION.md`, over the `docs/ROADMAP.md` current pointer. The pointer MAY lag behind it. |
| Any other conflict | None. Stop and ask the Owner (WR-2). |

An entry point (`CLAUDE.md`, `README.md`) routes to these files. It has no
precedence over any of them.

### The rules are binding

- Every rule in this file is binding in every repository that holds it. A rule about a
  document applies to each repository that holds that document.
- A document that contradicts a rule is defective. Fix the document.
- You MUST NOT bend, reinterpret, or locally override a rule.
- A Constitution MAY add a project rule. It MAY state an exception only where
  a rule in this file allows one.

### How a rule changes

| Rule | Change it with |
|---|---|
| Universal rule: an edit that is a significant, hard-to-reverse architectural decision, or that changes a shared contract | An Accepted ADR in `mikkiola/tooltempest`, and the edit to this file in the same change. |
| Universal rule: any other edit | The edit goes directly into this file. Before you finish, run the check of the rule's owner and its direct consumers ("Documentation maintenance"). The commit message states what changed. |
| Project rule | A Constitution amendment: an Accepted ADR in that repository, and the edit to `docs/CONSTITUTION.md` in the same change. |

- A consumer receives a changed universal rule through an explicit repin. This
  file belongs to the contract component. The consumer contract states what the
  repin needs.
- The shared contract is the interface between ToolTempest and its consumers: `docs/tooltempest/consumer-contract.md` and the formats that consumers depend on (the lock file, the receipt, and the plan document that `scripts/tooling.py materialize` prints). It is the part of the contract component that consumers depend on. The content of this file is not the shared contract. An edit of a universal rule is an ordinary edit unless it is a significant, hard-to-reverse decision, or it also changes the shared contract.
- A rule changes rarely and deliberately. You MUST NOT change a rule as a side effect of other work.
- Session-end automation MUST NOT edit `docs/CONSTITUTION.md`.

### Canonical documents

| Document | Function | MUST NOT contain |
|---|---|---|
| `SYSTEM_GOALS.md` | The Owner's strategic goal registry for all repositories, in the workspace root beside the repository directories. Stable goal IDs. Russian. | A copy in any repository. |
| `docs/CONSTITUTION.md` | How work happens in this repository: project-specific working rules. It governs behavior, not content. Role, session specifics, named exceptions. | A universal rule. Product behavior. The plan. Open questions. |
| `docs/PROJECT.md` | Why the system exists: goal, provable outcome, Definition of Done, boundaries. | Component status. Phases. Tasks. Rationale. |
| `CURRENT_MISSION.md` | The one active workstream. | Goal text. Content another document owns. |
| `docs/ARCHITECTURE.md` | Current state: components, status, dependencies, validation, commit. | Tasks. The plan. Rationale. History. |
| `docs/ROADMAP.md` | Phases, order, dependencies, current pointer. | Task detail. Acceptance criteria. Rationale. |
| `docs/BACKLOG.md` | Open tasks and Owner decisions needed. | Rationale of a made decision. Current state. The plan. |
| `SPEC.md` | The specification of the active task. | Content for more than one task. |
| `docs/adr/NNNN-<slug>.md` | One decision and the facts it rests on. | Anything else (DR-9). |
| `docs/adr/ADR-INDEX.md` | The list of ADRs with status and supersession. | A hand edit, where a generator exists. |
| `README.md` | What the repository is and how to set it up. | A rule. |
| `CLAUDE.md` | Entry point. | A rule, an architecture, a roadmap, or a goal list. |

- A project repository has a system-level goal of its own. It MUST hold
  `docs/PROJECT.md`. A tooling repository, such as ToolTempest, holds
  `README.md`, `docs/adr/` and this file. A tooling repository that gains a
  goal of its own becomes a project repository.
- A new repository SHOULD NOT create a `CHANGELOG.md`. `docs/adr/` and
  `git log` record what changed and why. Before you remove an existing one,
  read it and flag any content that `git log` and `docs/adr/` cannot recover.
  Do not assume that it transfers, and do not delete it silently.

### ADR acceptance

1. The decision is stated in Russian, in Google developer documentation style:
   active voice, second person, present tense, short sentences. The Owner
   states it, or you state it for the Owner's answer.
2. After the Owner's yes, write the ADR in English with status `Accepted`, and
   update its index (DR-12).
3. The content of the ADR is bounded by DR-9.

## Key words

MUST, MUST NOT, SHOULD, SHOULD NOT, and MAY have the meaning that RFC 2119
gives them. An imperative sentence that states an obligation without a key
word is a MUST.

| Key word | Meaning |
|---|---|
| MUST, MUST NOT | An absolute requirement or prohibition. |
| SHOULD, SHOULD NOT | A requirement that you MAY set aside only for a valid, stated reason. |
| MAY | An option. |

## Text rules

| ID | Rule |
|---|---|
| TX-1 | Documentation MUST live in Git as Markdown and pass the repository's hooks and CI. |
| TX-2 | A document MUST have one type: reference, explanation, how-to, or tutorial. |
| TX-3 | A document SHOULD use fixed headings, fields, tables, and stable IDs. A field SHOULD replace prose that adds nothing. |
| TX-4 | A fact MUST live in one place. Other places link to it. |
| TX-5 | An obligation MUST use a key word. |
| TX-6 | A rule states the obligation and its condition. Use the shortest text that keeps the meaning. A document MUST NOT carry history, motivation, or a repeated fact; Git and the ADRs hold them. The ADR sections of DR-7 and the `Found` and `Source` lines of BL-3 are the exceptions. |
| TX-7 | The reader and user of every tool, document, and repository artifact is an AI agent. The design criterion is that an agent finds the artifact, reads it without ambiguity, and acts correctly. This rule does not authorize deleting, rewriting, or converting an existing artifact. Conversation with the Owner is in Russian (LG-2). |

## Decision records

| ID | Rule |
|---|---|
| DR-1 | One decision MUST have one file, `docs/adr/NNNN-<slug>.md`. |
| DR-2 | Numbers are sequential per repository. You MUST NOT reuse or renumber them. Before you assign a number, list the ADR numbers on `origin/main`, in every local branch, and in every worktree. |
| DR-3 | `status` is one of `Proposed`, `Accepted`, `Deprecated`, `Superseded`. |
| DR-4 | You MUST NOT edit an Accepted ADR. A changed decision is a new ADR that supersedes the old one. |
| DR-5 | The new ADR sets `supersedes`. The one permitted edit of an Accepted ADR is its `superseded_by` field, which is cross-reference metadata, made by the change that adds an ADR that supersedes it in full. The old `status` and body stay as accepted. |
| DR-6 | A new ADR MAY supersede one point of an Accepted ADR. It names the point. The older ADR stays `Accepted` and is not edited, `superseded_by` included. The new ADR prevails on that point. |
| DR-7 | A new ADR MUST have the sections Status (a `Status:` line is enough), Context & Constraints, Decision, Alternatives & Rationale, Consequences, Confirmation & Revisit, Source, and the metadata fields of the repository's latest Accepted ADR, if it has any. Source names the session, including an undocumented earlier session formalized after the fact, and any external AI cross-check. Do not migrate existing ADRs to a new shape. Alternatives & Rationale states why each rejected option was rejected. Confirmation & Revisit states how the decision was verified, if verification is meaningful for it, and the grounds to revisit it. |
| DR-8 | An ADR SHOULD fit on one to two pages. Split a longer one: it holds more than one decision. |
| DR-9 | An ADR MUST NOT contain a specification, an acceptance-criteria list, a test list, a command list, or a snapshot of repository state. Decision MUST NOT hold a commit-bound fact (a count, hash, line number, or a path that is not the subject of the decision). Context & Constraints holds an observation the decision depends on. Confirmation & Revisit holds confirmation evidence. Label each with its command and commit. A command that prints removed text is evidence and is allowed. Put any other snapshot in the commit message. State a claim about repository state only with its command or source. |
| DR-10 | When canon gives a sufficient basis for one outcome, decide, and write the ADR in the change that implements the decision. This includes a decision about the Constitution, the component list, or the phase order. Otherwise the decision is the Owner's (WR-2, "ADR acceptance"). |
| DR-11 | `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, and `docs/CONSTITUTION.md` MUST NOT cite an ADR by number. Describe the outcome. `docs/BACKLOG.md` MAY cite ADR numbers. |
| DR-12 | Where an index generator exists, regenerate `docs/adr/ADR-INDEX.md` in the change that adds or edits an ADR. You MUST NOT edit a generated index by hand. Wire the generator into the workflow that adds or edits ADRs. Track a missing or unwired generator as a BACKLOG entry. Without a generator, update the index by hand, as part of the ADR write, in the same change. Fix a wrong index in the generator or in the ADR. A repository that holds no index MAY skip it. |
| DR-13 | You MUST NOT delete an ADR file without a separate Owner authorization. |

## Commit trailers

| ID | Rule |
|---|---|
| CT-1 | A commit that closes a BACKLOG entry MUST carry `Closes: B-NNN`, one trailer line per entry. |
| CT-2 | All trailers MUST form one contiguous block at the end of the message, with no blank line inside it. Add them with `git commit --trailer`. |
| CT-3 | Read trailers with Git's parser (`git log --format="%(trailers:key=Closes,valueonly)"`), never with a text search. A mention of an entry elsewhere in the message is not a closure. |
| CT-4 | A trailer or a commit does not prove that a criterion is met. The proof is a check result proportionate to the criterion. For a simple criterion, a passing test or a directly observed result is enough. |

## Working rules

| ID | Rule |
|---|---|
| WR-1 | Decide an ordinary technical or architectural question yourself when canon (this file, the Constitution, the ADRs, the code, the task) gives a sufficient basis. Report the choice. A decision clearly inside the task's scope with a defensible default also proceeds, with a report. |
| WR-2 | Stop and ask the Owner one specific question when the task admits principally different results (not different implementation details of the same result) and canon gives no basis to choose, when the work reaches outside the task's scope, or when documents conflict and "Where the rules live" does not resolve it. How large the decision feels is not a trigger. Example: "add JSON export" needs no question; "choose JSON, CSV, or a custom format" with nothing favoring one does, because it is a goal choice and an invented answer invents intent. |
| WR-3 | Present an architectural fork to the Owner as two or three options in a table: pros, cons, risks. |
| WR-4 | When a task makes `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`, `README.md`, or an ordinary fact in `docs/BACKLOG.md` stale, update the document directly in the same task. Closing a BACKLOG entry follows WR-5. |
| WR-5 | Close a BACKLOG entry only on the Owner's go-ahead or in a session-end step that the Owner starts. You MUST NOT decide by yourself that a session is over. Whether a task is done fully or in part, and which of several entries a commit closes, is a judgment: tag the commit, hold the `Closes:` trailer (CT-1), and ask before you write it. |
| WR-6 | Test-driven development is not a blanket requirement. Write the test first when inspection cannot cheaply verify correctness, when a wrong implementation is expensive to discover later, or when the mechanism's whole job is a judgment or a trigger under specific conditions, so that "does it trigger" is the central risk (for example a discovery, parsing, classification, or gating function). Confirm that the test fails for the right reason, then implement. State the reasoning for the test-first decision in the report. |
| WR-7 | Run sensitive operations (`git push`, token revocation, deleting a lock or temp file that another process may depend on) only from Claude Code on the real machine, never from a sandboxed or browser environment. |
| WR-8 | Before you add a component or capability, search for an existing one: the code, `docs/adr/`, the package registry, GitHub. Do not guess from memory. A one-line note of what you checked and found is enough; a bare "I confirmed nothing exists" is not. |
| WR-9 | Before a diff that changes operation state, success or failure semantics, a threshold, or prompt or instruction structure, search the whole codebase for related references. |
| WR-10 | State uncertainty as uncertainty, in plain words and not as a tag buried in the text. Label a hypothesis as a hypothesis. |
| WR-11 | Draft a prompt for an external AI cross-check neutrally. Do not steer it toward the position the project already holds. |
| WR-12 | A task for Claude Code MUST state its scope (specific files or paths, not only a topic), what is out of scope, what must not be touched, and the exact report format. A read-only task MUST be labelled read-only; on ambiguity it stops and reports and does not guess. The result is command output or the task's final report. No permanent file or planning layer is required unless the existing workflow requires one. |
| WR-13 | The task prompt is the approval. A task for Claude Code states its scope (WR-12). Inside that scope, write, edit, delete, and move files, and make a local commit, without a per-action question. Before a commit, check the full diff and run the prescribed checks. A commit with MEDIUM or LOW findings is allowed when the report lists them: a local commit saves state and does not mean readiness. A BLOCKER or HIGH finding blocks a commit (RV-7), and a review gate that the Owner requires before a commit still applies. A blocking hook still blocks; never bypass a hook. Outside the scope, stop and ask one question (WR-2). These actions always need an explicit question: token revocation, deleting a file that another process may depend on (WR-7), and any edit of a Constitution ("How a rule changes"). Deleting an ADR file follows DR-13. An edit of `.tooltempest.lock` follows the consumer contract: a task prompt that names a repin authorizes it under the conditions stated there, and the contract names the cases that need a question. Put a question in this form: `[ЗАПРОС] Действие: X. Путь: Y. Подтвердить? (Y/n)`. |
| WR-14 | Any `git push` needs a separate explicit permission of the Owner. Ask only after the mandatory checks pass. Write the request in Russian and state what exactly will be published, which checks passed, and which risks remain. If a mandatory check fails or the evidence is insufficient, push is forbidden and you do not ask. The permission covers only the change set that you showed; a changed set needs a new permission. The permission authorizes publication; it does not confirm technical correctness. |
| WR-16 | To verify an instruction-only change, check the links, the scope, the exact diff, and `git diff --check`. You MUST NOT run a production entry point, dispatch a workflow, or call a live external service to validate instruction text. |
| WR-17 | An instruction-only task MUST NOT change settings, permissions, hooks, or MCP configuration. |
| WR-18 | Reuse the installed shared skills. You MUST NOT copy a skill's procedure into a project agent, or add a second skill for the same procedure. |

## Review and verification

| ID | Rule |
|---|---|
| RV-1 | A report MUST name the entry mode of the work, and state the commands run, their literal output, the commit SHA when a commit was made, and that every "do not touch" item is untouched. "Done" or "tests pass" without output is not a report. Ask for the literal output before you treat a task as verified. |
| RV-2 | Whatever the entry mode, after a change, compare the final state with the starting state and run the checks that apply. Depth follows the size, scope, and risk of the change. A small local edit names the areas it touched and the material checks it did not run, and needs no table of all document categories. A change to architecture, an interface, configuration, several repositories, or irreversible state checks the relevant dependencies and documents, and edits only those that became wrong. No heavy report per task. |
| RV-3 | Use four results: PASS (confirmed), FAIL (confirmed failure), SKIP (not run or not runnable, with the reason), N/A (justified inapplicability). A check that did not run is never a PASS. |
| RV-4 | Report separately: tests and checks, document impact, substantive review, acceptance of the stated criteria, publication, Owner acceptance. |
| RV-5 | Evidence stays valid until a later change touches what it covers. Re-check only that part. |
| RV-6 | After you migrate or rewrite a document, verify and report, as a separate step: re-read the original and confirm that every fact, decision, obligation, and open question has a home; the document follows its format in this file; no two documents contradict each other. |
| RV-7 | A review finding is HIGH only when you can state this chain: (1) the concrete defect; (2) who or what relies on it; (3) the specific wrong action that an agent or a consumer takes because of it, and the cost of that action. Qualifying actions: a push without the Owner's permission, a bypass of a review gate or a hook, the deletion of a file that another process depends on, a secret exposure, a consumer breakage, and the loss of an obligation that has a mechanism or an incident cost and has no other record. A false statement, a contradiction between documents, or a wrong cross-reference without that chain is MEDIUM. A BLOCKER or HIGH finding blocks a commit. MEDIUM and LOW findings never block a commit; list them in the report. After a HIGH fix, a re-review covers only the changed lines. A change that fixes only MEDIUM or LOW findings needs no re-review. The budget is two review passes per repository and per task; when the second pass still returns a BLOCKER or HIGH finding, stop, report, and do not commit. |
| RV-8 | The review severity levels are BLOCKER, HIGH, MEDIUM, and LOW. A BLOCKER or HIGH finding blocks a commit; a MEDIUM or LOW finding never does (RV-7). HIGH needs the chain of RV-7. A finding that is neither BLOCKER nor HIGH is MEDIUM or LOW, and both are advisory. A reviewer template MUST reference this rule and MUST NOT define the levels a second time. |

## Documentation maintenance

These rules apply when you create or change a document or an instruction.

- Read the sources that the task touches, and no others beyond the session-start reading order.
- Update the existing authoritative document before you create a new one.
- A rule has one owner. Refer to it by its Rule ID, or by file and section when it has no ID. You MUST NOT create a new ID or a registry of rules.
- A new or changed mandatory instruction states who acts, the trigger, the required or prohibited action, whose permission is required, the scope and conditions, and the exceptions. State a field only where it applies.
- Before you finish, check the change against the rule's owner and its direct consumers. Fix every defect that the change itself introduces.
- Report an inconsistency that was there before your change and lies outside its scope. Start no repair cycle for it.
- You MUST NOT change an Owner goal, an Owner priority, or an Owner-managed status.
- An ADR is also required for a significant decision that is hard to reverse, and for a change to a shared contract, in addition to the triggers of DR-10 and "How a rule changes". Touching several repositories does not by itself need an ADR.
- An independent review is mandatory only for a change of authority, a hook, security, an external contract, or a critical cross-repository interface. This sets the universal minimum. It does not reduce a review that the Owner or the workspace requires (WR-13).

| Document | Update trigger |
|---|---|
| `docs/BACKLOG.md` | BL-3 for the entry fields; WR-4 for a stale ordinary fact; WR-5 for closure |
| `CURRENT_MISSION.md` | MC-4 and MC-5 |
| `docs/ARCHITECTURE.md` | WR-4 and AR-7 |
| `docs/CONSTITUTION.md` and other operational rules | "How a rule changes" |
| `docs/adr/` | DR-10 and the ADR bullet above |

## Mission card

These rules apply to a repository that holds `CURRENT_MISSION.md` in its root.

| ID | Rule |
|---|---|
| MC-1 | The card holds one active workstream in the fields `GOAL`, `NOW`, `CURRENT STEP`, `DONE WHEN`, `IN SCOPE`, `OUT OF SCOPE`, `BLOCKERS`, `STATUS`. `STATUS` is `ACTIVE` while the work runs and `DONE` when `DONE WHEN` is met. |
| MC-2 | `GOAL` cites a stable ID from `SYSTEM_GOALS.md`. The card MUST NOT copy goal text. |
| MC-3 | You MUST NOT rewrite the goal registry, mark a goal `ACHIEVED`, or choose a successor goal. Write a possible achievement as a question for the Owner. |
| MC-4 | A new issue or idea enters the mission only when `DONE WHEN` requires it or the Owner adds it to `IN SCOPE`. Otherwise record it in `docs/BACKLOG.md` and continue. |
| MC-5 | Only the Owner switches the active task, at any time. The explicit switch reconciles `CURRENT_MISSION.md`; never re-interpret the scope silently in mid-session. |
| MC-6 | Meeting `DONE WHEN` sets `STATUS` to `DONE`. It does not mean that the goal is achieved. |

## Document formats

### ARCHITECTURE

| ID | Rule |
|---|---|
| AR-1 | One table, one row per component, with the columns `Component`, `Status`, `Depends on`, `Validation`, `Commit`. `Depends on` names components, not prose. |
| AR-2 | `Status` is `Implemented`, `Not started`, or a stated fraction or caveat. "In progress" alone is not a status. |
| AR-3 | `Validation` names what confirms that the component works: a test suite, a count of live cases, a run on real data. "Should work" and "looks correct" are not validation. |
| AR-4 | `Commit` holds the SHA that produced the current state. For vendored source, give the source commit and the vendoring commit. |
| AR-5 | Two more tables are allowed: `Repo`, `Contains`; and `Component`, `Model or service`. |
| AR-6 | Table cells only, no prose sections. State this at the top, in a preamble of at most five lines. |
| AR-7 | Every row MUST be true now. Change the row; do not add a second one. |
| AR-8 | A change to a component directory without a change to `docs/ARCHITECTURE.md` in the same push SHOULD trigger at least a warning from a check. |

### ROADMAP

| ID | Rule |
|---|---|
| RM-1 | One table `Phase`, `Status`. `Status` is `Closed`, `Not started`, or a specific blocked or unblocked state. It MUST agree with the rest of the file. |
| RM-2 | A current pointer: where work stands and what a session can pull next. Short prose is allowed here. |
| RM-3 | One table `Phase or Component`, `Depends on`, consistent with `docs/ARCHITECTURE.md`. Use a diagram only for a flow that a table cannot hold. |
| RM-4 | Open decisions are one line that points to the section "Owner decisions needed" of `docs/BACKLOG.md`. |
| RM-5 | Items have no IDs. An item that is concrete enough to start moves to `docs/BACKLOG.md`, and the roadmap keeps a one-line pointer to its phase. |

### BACKLOG

| ID | Rule |
|---|---|
| BL-1 | An entry has a stable ID `[B-NNN]` on its heading or bullet line. IDs are sequential and survive retitling. You MUST NOT reuse or renumber them. |
| BL-2 | When the scope of an entry changes so that its ID no longer describes it, close the entry and open a new one. Resolve the original with a note that explains the split, names the IDs of the new entries, and rests on what the entry's own text established, not on unverifiable external material. |
| BL-3 | An entry states a priority `P0` to `P3` or an equivalent scheme, a `Found:` line (date and context), the problem in enough detail for a reader with no other context, the next steps as a checklist or the stated reason for deferral, and a `**Source.**` line. Priorities are consistent across the file, and the file is sorted or grep-able by priority. |
| BL-4 | An entry whose content an external AI or an earlier session contributed, and that was not verified against the repository, MUST say so. It MUST NOT present that content as established fact. |
| BL-5 | A closed entry carries `— RESOLVED` in its title, and its closing commit carries the trailer of CT-1. |
| BL-6 | Fix a wrong closure with an ordinary follow-up commit. |

### PROJECT

| ID | Rule |
|---|---|
| PR-1 | The sections, in order: System Goal, System Outcome, Current vs. Target, Definition of Done, Boundary statements. System Goal is one paragraph that states what the system does and for whom, in outcome terms. Current vs. Target is coarse and links to `docs/ARCHITECTURE.md` and `docs/ROADMAP.md`. |
| PR-2 | System Outcome states the narrowest outcome that the system can prove by its own operation, precisely enough to exclude claims it cannot verify, and says what it cannot prove. |
| PR-3 | The Definition of Done is one bar for every target unit. Adding a unit MUST NOT require rewriting it. Which units meet it, and their status, live in `docs/ARCHITECTURE.md` and `docs/ROADMAP.md`. |
| PR-4 | Each boundary statement names one adjacent system and states that this repository does not implement, track, or duplicate its goal model, data, or state. A task on that concern belongs in the other system's repository. |
| PR-5 | `docs/PROJECT.md` MUST NOT change as a side effect of routine work. Only an Owner-directed change edits it. DR-10 does not apply to it. |

### SPEC

| ID | Rule |
|---|---|
| SP-1 | A repository has one project SPEC, the root `SPEC.md`. No repository has its own SPEC lifecycle. The DocOps checker always evaluates the root `SPEC.md`, with or without a policy file. A `/spec` session overwrites it, and creates or updates only that file. Its history is `git log -- SPEC.md`. |
| SP-2 | `/spec` runs only on the Owner's explicit command. Use it for an architectural decision or for work that reading the result cannot verify, not for prose, drafts, or brainstorms. |
| SP-3 | A SPEC that stays in the repository between tasks declares `spec-state: idle` in its frontmatter. |
| SP-4 | Another SPEC file exists only through an entry in `.docops-policy.json`: a registered active track, or a listed non-active file with its exact path and a reason. |
| SP-5 | Having no active SPEC is a normal state. It never permits skipping a safety, confirmation, review, or verification rule. Do not invent milestones for an absent or idle SPEC. |

The DocOps checker (`scripts/docops.py check`) gives these verdicts on the
SPEC state:

| State | Verdict |
|---|---|
| No `SPEC.md` anywhere and none registered | N/A |
| Root `SPEC.md` with `spec-state: idle` | N/A |
| A SPEC for active work that meets the structural contract | PASS |
| A SPEC for active work that does not meet it | FAIL |
| A tracked `SPEC.md`, at any depth and in any letter case, that is not the root SPEC, a registered track, or a listed non-active file | FAIL, naming the path and the rule. Never skipped silently, never N/A, never PASS. |
| A path in two categories; a duplicate or stale list entry; the root listed as non-active; a non-active entry without a reason; a registered path that is absent or a directory; a track named `root` at another path beside a root `SPEC.md`, when the policy has no root entry | FAIL |
| A corrupted or unreadable SPEC; a checker that is unavailable or did not run | FAIL, or a tool error with no per-check status and a non-zero exit. Never N/A, never PASS. |

## Language

| ID | Rule |
|---|---|
| LG-1 | Documents, ADRs, code, comments, commit messages, and prompts for Claude Code MUST be in English, in Google developer documentation style. The one exception is `SYSTEM_GOALS.md` (see "Canonical documents"), which the Owner maintains in Russian. |
| LG-2 | Conversation with the Owner is in Russian. |
| LG-3 | Product output uses the language of its audience. A Constitution MUST name each such exception. |
| LG-4 | A field label that code matches by exact string keeps its original language. Do not retrofit old files. |

## ToolTempest consumer obligation

`docs/tooltempest/consumer-contract.md` states the obligation and the repin
evidence. It is their single source.
