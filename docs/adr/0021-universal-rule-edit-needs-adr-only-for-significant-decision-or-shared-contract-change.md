# ADR-0021: An Edit of the Universal Rules Needs an ADR Only for a Significant Decision or a Shared-Contract Change

Status: Accepted by the Owner, 2026-11-11. It supersedes one point of ADR-0020 (DR-6): the sentence that requires an Accepted ADR for every edit of `docs/tooltempest/universal-rules.md`. ADR-0020 stays Accepted and is not edited.

## Context & Constraints

ADR-0020 states: "the rule is changed only by an Accepted ADR in this repository". Read literally, every edit of `docs/tooltempest/universal-rules.md` needs its own ADR, including a wording fix or a clarification.

## Decision

An edit of `docs/tooltempest/universal-rules.md` needs an ADR only if it is a significant, hard-to-reverse architectural decision or changes a shared contract (Owner decision of 2026-11-11). Any other edit goes directly into the file, with the check of the rule's owner and its direct consumers and a commit message that states what changed. This supersedes only the sentence of ADR-0020 that requires an ADR for every edit ("the rule is changed only by an Accepted ADR in this repository"). The "How a rule changes" table of the universal file states the two cases.

## Alternatives & Rationale

| Option | Result |
|---|---|
| Keep an ADR for every edit | Rejected: the Owner decided that ordinary edits go directly into the file. |
| Leave the trigger undefined | Rejected: an agent could not tell which edit needs an ADR. |

## Consequences

ADR-0020 stays Accepted and is not edited; this record prevails on the one sentence it names. An ordinary edit of the universal file needs no ADR, and its commit message is the record of what changed.

## Confirmation & Revisit

The two cases are in "How a rule changes" of `docs/tooltempest/universal-rules.md`. Revisit when an edit cannot be classified under the two cases.

## Source

The Owner's decision of 2026-11-11. No external AI cross-check.
