# ADR-0019: Retiring a Path of an Installed Root

Status: Accepted, 2026-11-11.

## Context & Constraints

ADR-0017 holds that no path of an installed root leaves the manifest until a separate accepted ADR defines how removed paths are handled, and expects the first case at legacy retirement. Today a consumer's lock is `{repository, commit, path, sha256}`, where `path` names one script of the root that is to be retired.

Observations at commit `fecae4d`; the tests that pin them are named under Confirmation & Revisit. A path the lock declares but the pinned manifest does not install is an error on `verify`, `materialize` and `materialize --apply`, and nothing is written. A path the lock does not declare stays on disk and leaves the receipt once it has left the manifest. The materializer deletes only temporary files its own run created (ADR-0015). A lock with only `repository` and `commit` is valid; the hash of every installed file then comes from the install receipt, which ADR-0015 accepts as a statement of later divergence, not of the intended release.

The completeness check requires the manifest and the tracked files of the composition directories to agree, so a manifest line cannot leave while its file stays tracked. The consumer contract lets a core repin proceed without a separate Owner decision only when each differing payload path is a line of the new manifest; a removed path is not.

## Decision

A path of an installed root leaves the manifest only in one commit that deletes the file and drops its manifest line, after the Owner has decided that nothing a consumer will pin from then on needs it: a replacement exists at or before that commit, and the hooks and scripts of each consumer at the pin it will adopt no longer call the path. The removal is never a side effect of another change.

Removing a path changes no consumer. A consumer keeps its pin and its installed copy until it repins. A repin across a removal is an Owner decision under the consumer contract, because the removed path fails the test that differing paths be lines of the new manifest. This record does not change the contract.

The materializer never deletes the installed copy of a removed path. The consumer deletes it in the commit that repins across the removal (for the client target, the operator of the named root does). Nothing else cleans it: after that apply the copy is not in the receipt, and `verify` raises no finding for a copy left behind.

The lock format is unchanged, and this record does not require a lock without declarations. A lock that still declares the removed path fails closed at the new pin, so in the same commit as the repin across the removal the consumer either drops its declarations or re-declares a surviving file, with the existing `path` and `sha256` pair or in `files`. The consumer whose own lock guard needs a declared pair chooses that file; this record names none. A hash declared for a surviving file is edited by its consumer each time the bytes of that file change; where nothing is declared, local integrity and provenance rest on the receipt.

## Alternatives & Rationale

| Option | Verdict | Why |
|---|---|---|
| Engine tolerates a declared path that left the manifest | Not chosen | A wrong declaration and a retired one become indistinguishable without a new lock field or a new engine rule; ADR-0017 fixes the lock format and the engine fails closed today. |
| Engine deletes copies listed in the receipt | Rejected | ADR-0015 makes deletion ownership-based and limited to the run's own temporary files; the consumer may have edited the copy. |
| Keep per-file hashes in the lock and edit them at every repin | Rejected | Every payload change then needs a lock edit or `verify` reports a provenance mismatch; this is the cost to remove. |
| Never remove a path | Not chosen | Code with no consumer stays in every install and in every future pin. |

## Consequences

- A consumer's lock loses the one hash that its own Git history declared independently of the pin. Provenance of every file then rests on the pinned commit id and the receipt, which is local to the checkout. A fresh clone or worktree has no receipt, `verify` exits 1 until `materialize --apply` records files that equal the pin, and a file that differs is refused as unmanaged.
- Each consumer's repin across a removal is a separate Owner decision.
- A copy a consumer forgets to delete stays executable and unreported by any ToolTempest command.
- An engine rule that names retired paths, or lists leftover copies, is a separate decision and is not made here.

## Confirmation & Revisit

At commit `fecae4d`, `cd tests/tooling && python3 -B -m unittest test_formats.LockParsingTests.test_a_lock_with_no_declarations_is_valid test_verify.ThreePropertiesTests.test_a_clean_installation_is_established_on_every_property test_verify.ThreePropertiesTests.test_local_integrity_can_fail_while_provenance_holds test_verify.EnvironmentAndInputEdgeTests.test_a_tampered_receipt_hash_is_a_provenance_failure_and_blocks_materialize test_verify.VerifyExitTests.test_a_structural_error_fails_verify_even_when_every_installed_file_is_fine test_materialize.RepeatedRunTests.test_a_lock_declaring_a_path_the_manifest_does_not_install_is_refused test_materialize.RepeatedRunTests.test_applying_after_a_manual_hand_install_adopts_without_rewriting_a_byte test_materialize.UpgradeTests.test_a_file_dropped_from_the_manifest_is_left_in_place_and_leaves_the_receipt` ran 8 tests, all OK. In `tests/distribution`, `test_manifest_check.NegativeCaseTests.test_a_stale_entry_for_a_file_that_is_gone_is_reported_by_name` and `test_manifest_check.VariantsOfTheRealManifestTests.test_every_real_entry_is_needed_dropping_each_one_fails` also pass.

A disposable fixture outside any repository (isolated `HOME`, `GIT_CONFIG_GLOBAL=/dev/null`, a fixture upstream with three commits, a fixture consumer) gave the following with a lock of only `repository` and `commit` and a complete receipt: `verify` reported local integrity and provenance `ok` for every file, with the hash declared by the receipt; the `materialize` plan and `--apply` were `current` and wrote nothing. Across the removal, a lock with `path` and `sha256` exited 1 with `DECLARED_PATH_NOT_INSTALLABLE` on `verify`, plan and `--apply`; a bare lock exited 0, left the copy on disk and out of the receipt, and `verify` reported nothing for it. No test pins the last observation; the code that produces it is `_build_items` in `scripts/tooling_core/engine.py`. The fixture is not kept in the repository.

Revisit this decision at the first actual removal; when a copy left after a retirement is found running; or when the engine gains a way to name retired paths.

## Source

The Owner's instructions to Claude Code of 2026-11-10, derived from ADR-0015, ADR-0017 and the observations above. No external AI cross-check was made.
