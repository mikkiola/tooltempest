# ADR-0017: Distribution Model for DocOps — Components, Versions and Delivery

Status: Proposed, 2026-11-04.

## Context & Constraints

A consumer pins ToolTempest by commit, but what it uses is content. Freshness is commit distance (`_freshness` in `scripts/tooling_core/engine.py`), so a commit that changes nothing a consumer installs still makes the pin look behind. Content reaches consumers by two installers. The materializer (`scripts/tooling.py`) installs only `scripts/` and `schemas/` and writes a receipt; its destination roots are fixed in the manifest mapping (`parse_manifest`), in the receipt parser (`parse_receipt`) and in a closed receipt schema that accepts only version 1.

A file that leaves the manifest stays on disk and raises no finding (`_build_items` and its existing test), except that a path the lock declares is an error. Each consumer also keeps its own installer script, `sync-tooling.sh`; its pre-push hook reaches the network with `git ls-remote`; and item 7 of the Constitution section of the documentation rules is copied by hand into each Constitution with nothing comparing the copies. These three facts are observations from an audit that this repository does not contain. The lock format is not changed by this record, and ADR-0015 is accepted and is not edited.

## Decision

A version is the content of a component, not a commit: the Git-tree content of its paths in a snapshot. Freshness, once built, is judged per component, not by commit distance. Every fact has one source; the consumer obligation of item 7 moves into one contract file instead of being copied by hand.

There are three components: core (`scripts/`, `schemas/`), contract (one consumer-facing file extracted from item 7, in its own root under `docs/`, separate from `docs/reference/`) and client (`skills/`, `rules/`). Core and contract are delivered by the existing materializer. The client is delivered by an explicit, separately invoked target of the same materializer and is never run implicitly.

Consumer hooks make no network calls. The `git ls-remote` drift warning in consumer pre-push hooks is to be removed; each consumer decides that in its own ADR.

The lock format is unchanged. The receipt and the install journal get one additive new version.

No path of an installed root (`scripts/`, `schemas/`, the contract root, `skills/`, `rules/`) leaves the manifest until a separate accepted ADR defines how removed paths are handled.

Repin evidence is per component. For core it is the evidence defined in the documentation rules; a change to the contract or to the client needs the Owner's approval of the diff.

Out of scope: rollout APPLY, CI bots, plugins, tags, an engine cache, a SPEC generator, and engine code for the fingerprint and for freshness. Outside the system, claude.ai project instructions and assistant memory hold references only.

Where ADR-0017 and ADR-0015 cannot hold together, ADR-0017 prevails; ADR-0015 is not edited.

## Alternatives & Rationale

- A commit as the version (today): rejected, because a commit that touches nothing a consumer installs shows as behind.
- A digest stored in the lock or receipt: rejected, because it adds a stored field that can go stale against the tree and changes the lock format.
- A separate client adapter with its own installer: rejected, because it is a second installer beside the materializer and the consumers' own script, with a second receipt format.
- Widening receipt version 1 in place: rejected, because the parser and the schema are closed to version 1, so a consumer that needs nothing new would hold a receipt that older tools cannot read.
- `git ls-remote` in hooks: rejected, because it puts the network in a hook and a string comparison of commits says nothing about what changed.
- The contract inside `scripts/` or `schemas/`: not chosen, because a change to a consumer-facing document could not then be told from a core change by root.
- Defining removed-path handling now: not chosen, because no path of an installed root has been removed (see Confirmation & Revisit), so the rule waits for the first case.

## Consequences

- Until engine code for the fingerprint and for freshness exists, freshness stays commit distance.
- Source text and documents that say the tool installs nothing outside the consumer repository will be false once the client target is built.
- A receipt in the new version is refused by an older tool.
- The first removal of a path of an installed root needs its own accepted ADR first.

## Confirmation & Revisit

Revisit this decision when a fourth consumer repository appears, counted by origin URL; when a second incident of a false version signal, or of a repin for an unchanged payload, occurs within a quarter; when a second AI client is added; or at the first removal of a path of an installed root from the manifest, expected at legacy retirement.

Two observations, taken at commit `6510b7554f4c406d3603b8f9792396a3c766bb4d`. `git log -p --format= -- MANIFEST.txt | grep '^-[^-]'` prints one line, `-docs/reference/canonical-documentation-bible.md`: commit `a59d9aa249a0be68e8ed329f857cb77971fd54af` removed that reference-only entry, which consumers never install. `git log -p --format= -- MANIFEST.txt | grep -c -E '^-(scripts|schemas|skills|rules)/'` prints 0: no path of an installed root has been removed so far (the contract root does not exist yet).

## Source

The Owner's instructions to Claude Code of 2026-11-04. No external AI cross-check was made.
