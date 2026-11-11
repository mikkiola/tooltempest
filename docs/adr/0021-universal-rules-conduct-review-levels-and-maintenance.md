# ADR-0021: Universal Rules Gain Conduct, Review-Level and Documentation-Maintenance Rules

Status: Accepted by the Owner, 2026-11-11. It supersedes one point of ADR-0020 (DR-6): the sentence that requires an Accepted ADR for every edit of `docs/tooltempest/universal-rules.md`. ADR-0020 stays Accepted and is not edited.

## Context & Constraints

The Owner requires one source of general rules for every agent of four repositories (statements of 2026-11-11, recorded by the architect in the task prompt, not quoted). After ADR-0020 the Constitutions of `article-pipeline`, `brain` and `radar` still restate general statements in their own words. Observation at commit `8c0dd0e`: `git grep -n -i "instruction-only\|instruction-edit\|maintainer-instruction" 8c0dd0e -- CLAUDE.md` prints a statement on the verification of an instruction-only change in `CLAUDE.md` of `tooltempest`. The same statement in the other three repositories is not re-verified here: the Constitutions of `article-pipeline`, `brain` and `radar` restate rules in their own words, per the audit recorded in ADR-0020.

The severity levels BLOCKER, HIGH, MEDIUM and LOW are named in the global reviewer template, which this repository cannot edit; only HIGH has a definition in this repository (RV-7). The trigger for BLOCKER is defined nowhere in the four repositories.

The Owner's requirements 4, 5 and 8 of that record state the documentation-maintenance standard.

## Decision

`docs/tooltempest/universal-rules.md` gains: WR-15 to WR-19 (neighboring repositories, verification of an instruction-only change, no configuration change by an instruction-only task, reuse of shared skills, secrets); TX-7 (the audience of every artifact is an AI agent, and the design criterion); RV-8 (the review levels, named and bounded once, with HIGH defined by RV-7); and the section "Documentation maintenance".

Transfers, whose meaning all four entry points already state in different words: WR-17, WR-18, and the verification steps of WR-16. New general rules, stated by three entry points or fewer: WR-15 (three), the prohibition in WR-16 (three), WR-19 (partly, in two). Their owner is the Owner's decision to accept this ADR. A statement with a project-specific component stays where it is.

An edit of `docs/tooltempest/universal-rules.md` needs an ADR only if it is a significant, hard-to-reverse architectural decision or changes a shared contract (Owner decision of 2026-11-11). Any other edit goes directly into the file, with the check of the rule's owner and its direct consumers and a commit message that states what changed. This supersedes only the sentence of ADR-0020 that requires an ADR for every edit ("the rule is changed only by an Accepted ADR in this repository"). The "How a rule changes" table of the universal file states the two cases.

`docs/tooltempest/consumer-contract.md` gains one sentence: a change to ToolTempest source does not repin, install or activate anything for a consumer.

This ADR defines no BLOCKER trigger. That trigger is the Owner's decision.

## Alternatives & Rationale

| Option | Result |
|---|---|
| Leave each statement in each repository | Rejected: the copies already differ in wording, and a change must be made four times. |
| Copy the four Constitutions into the universal file | Rejected: it carries project rules into every repository (ADR-0020). |
| Add the rules and invent a BLOCKER trigger | Rejected: the trigger is an Owner decision. |

## Consequences

Accepted means decided and recorded in local history, not delivered. Consumers receive the rules only through an Owner-decided repin (consumer contract). Until then the consumer copies stay active. The statements removed from the consumers later need a separate, per-statement transfer record.

## Confirmation & Revisit

To be confirmed at the commit that adds this file, with the literal output recorded in the report of that change, by `python3 scripts/check_manifest.py` and the three test suites of `CLAUDE.md`. Revisit when the Owner defines the BLOCKER trigger, or when a fifth repository states a different meaning of any added rule.

## Source

The task prompt of 2026-11-11 (architect's record of the Owner's statements). No external AI cross-check. No Brain record exists: Brain's rules forbid a Claude-authored atom body.
