# ADR-0018: Install-Lock Placement for Roots That Are Not Their Own Work Tree

Status: Accepted, 2026-11-05.

## Context & Constraints

ADR-0015 puts the install lock of `materialize --apply` in the checkout's Git directory (`<git dir>/tooltempest-install.lock`) and rejects a lock file inside the work tree, because it would appear in `git status`. ADR-0017 adds a client target whose root is an explicit directory. That directory need not be a Git checkout, and it can lie inside the work tree of a larger repository.

Observation at commit `c8376ce`: the engine asked Git for the Git directory of any root, so a root nested in another repository's work tree wrote its lock into that repository's Git directory, and any failure of `git` silently selected a lock inside the root. Commit `84519ca` replaced this with the rule below.

## Decision

The install lock of a root that is the top level of its own Git work tree stays in that repository's Git directory, as in ADR-0015. Any other root (outside Git, or inside another repository's work tree) gets `.tooltempest-install.lock` inside the root, opened with `O_NOFOLLOW`.

If `git` cannot say where the root belongs (missing, dubious ownership, bare repository, timeout), `--apply` stops with `GIT_FAILED` before any write. The message "not a git repository" means a plain directory.

ADR-0015 is not edited. Where the two cannot hold together, ADR-0018 prevails for roots that are not the top level of their own work tree.

## Alternatives & Rationale

- A lock in another repository's Git directory: rejected, because it writes into a foreign Git directory.
- Refusing such roots: not chosen, because it blocks legitimate use of a root nested in a larger repository.
- A lock outside the root: rejected, because it is not tied to the root.

## Consequences

- For a root inside another repository's work tree, the lock is an untracked file in that tree, visible in its `git status`, and it stays after the run.
- A tool version that places the lock in the Git directory and one that places it in the root do not exclude each other on such a root.
- The Git-directory lock is not opened with `O_NOFOLLOW`; this is unchanged.
- A root that is its own repository, and a plain directory outside Git, keep the lock they had.

## Confirmation & Revisit

At commit `84519ca`, `cd tests/tooling && python3 -B -m unittest test_targets.InstallLockPlacementTests test_targets.InstallLockOutsideGitTests` ran 11 tests, all OK. They cover a root nested in another repository, a root typed in another letter case, a failing and a missing `git`, a symlink, a directory and a FIFO at the lock name, and a held lock.

Revisit this decision if the untracked lock file in an enclosing repository is reported as a problem, or when it is decided whether the Git-directory lock must be opened with `O_NOFOLLOW`.

## Source

The Owner's instructions to Claude Code of 2026-11-05, recording the behaviour of commit `84519ca` after two review passes. No external AI cross-check was made.
