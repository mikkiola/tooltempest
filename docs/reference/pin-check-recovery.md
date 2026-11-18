# Pin Check Recovery

| Field | Value |
|---|---|
| Type | Reference |
| Reader | An agent in a consumer repository whose commit or push was blocked by `scripts/tt_pin_check.py`, and the Owner |
| Source | `scripts/tt_pin_check.py` and `scripts/tooling_core/engine.py` at the commit that holds this file; ADR-0022 Decision 5 |
| Delivery | Not installed (ADR-0008). Read it in a ToolTempest checkout whose `HEAD` is the pinned commit (`git -C <checkout> rev-parse HEAD`). A pin that precedes the commit that added this file does not contain it. |

This file adds no rule for an agent. The section "Owner emergency procedure" records
a decision of the Owner that applies to the Owner only. The rest states what each
block means and applies WR-2, WR-13 and WR-17 of `docs/tooltempest/universal-rules.md` and the repin
conditions of `docs/tooltempest/consumer-contract.md`.

## What the check decides

A consumer hook runs `python3 -B scripts/tt_pin_check.py commit --root <repo>`
before a commit and `... push --root <repo>` before a push. Exit 0 means the
install matches the commit pinned in `.tooltempest.lock`, or the tip has no
lock and is skipped. Exit 1 means blocked. Exit 2 means a usage or internal
failure and blocks as well. A block prints `[tt-pin] BLOCKED: <label>: <reason>`;
`<label>` is `index` for a commit and the pushed ref for a push. Shown by: the
module docstring of `scripts/tt_pin_check.py`, and
`tests/tooling/test_pin_check.py`.

The check reads Git objects from `--source` (default `~/.cache/tooltempest/repo`).
It never fetches, and it writes nothing in the repository, the lock or the cache; it
keeps a copy of the lock bytes in a temporary directory while `tooling.py verify`
runs (docstring of `scripts/tt_pin_check.py`). Facts are read in a fixed order and the first
one that does not hold decides: the files exist, the installed
`scripts/tooling.py` equals the pinned blob, every file the lock declares has the
declared sha256, `tooling.py verify` exits 0.

## Block messages

"Printed fix" is the `To fix:` text at the end of the message:
`python3 <ToolTempest checkout>/scripts/tooling.py materialize --root <repo> --source <source> --apply`,
with `(add --allow-network to fetch exactly <pinned commit>)`. `<ToolTempest checkout>`
is a ToolTempest checkout whose `HEAD` equals the `commit` of `.tooltempest.lock`
(`git -C <checkout> rev-parse HEAD`). Use its `scripts/tooling.py`, not the
installed one: the installed one may be the stale or missing file.

| The message contains | It means | Fix | The agent fixes it |
|---|---|---|---|
| `cannot read the pinned scripts/tooling.py from <source>: <reason>` | The check cannot read the pinned commit. `<reason>` `is not a directory` or `not a git repository`: the cache path is absent or is not a clone. `object <sha> is not in the source`: the cache lacks the pinned commit. Both are the empty cache. | The printed fix, with `--allow-network` (see "Run the printed fix"). | Yes |
| `<paths> is missing or not a regular file` | A file the lock declares, `scripts/docops.py`, `scripts/tooling.py` or `.tooltempest.receipt.json` is absent, a symlink or a directory. A fresh clone is this case. | The printed fix. | Yes, unless the dry run plans `refuse` |
| `the installed scripts/tooling.py differs from the pinned one` | The installed bytes are not the pinned bytes: the pin moved without a reinstall, or someone edited the file. | The printed fix. | Yes, unless the dry run plans `refuse` (`CONFLICT_MANAGED_MODIFIED`: the file was edited) |
| `<path> differs from the lock, or the lock does not declare it` | The sha256 of an installed file differs from the one in the lock, or the lock does not declare the checker. | The printed fix. If the block stays after the install, the lock and the pinned commit disagree. | Install: yes. A block that stays: no, ask |
| `` `tooling.py verify` exited N `` | The installed file set differs from the install receipt or from the pinned objects. The summary lines name the cause. | The printed fix. | Yes, unless the dry run plans `refuse` |
| `the lock is not usable: <detail>` | The lock in the index or in the pushed tip does not parse. No fix is printed. | Correct the lock edit that this task staged. | Only an edit that this task made. Otherwise no, ask |
| `.tooltempest.lock is unmerged, or is a symlink or a submodule, in the index` | The index entry of the lock is not a regular merged file. No fix is printed. | Resolve the merge that this task started. | Only a merge that this task started. Otherwise no, ask |
| `the pushed tips pin different locks` | One push carries refs with different locks; one installed checker cannot match both. | Push the refs one at a time. | Yes |
| `the input is not a pre-push line` (exit 1), `BLOCKED (internal error)` (exit 2) | A hook wiring problem or an internal failure. No fix is printed. | None. | No, ask |

Shown by: `tests/tooling/test_pin_check.py` (one test per row), and a run on
2026-11-18 of `python3 -B scripts/tt_pin_check.py commit --root <consumer>
--source <dir>` with `<dir>` an absent path, an empty directory and an empty Git
repository: exit 1 each time, with the three `<reason>` texts above. The same
run with `push` (one pre-push line on standard input) exits 1. With the default
cache it exits 0 and prints `verified, the install receipt matches the pinned commit`.

## Run the printed fix

1. Run the command without `--apply`. It is a dry run: it fetches nothing even
   with `--allow-network` (shown by `test_allow_network_without_apply_fetches_nothing`
   in `tests/tooling/test_network.py`). Read the plan.
2. Continue only if it exits 0 and plans no file as `refuse`
   (`CONFLICT_MANAGED_MODIFIED`, `CONFLICT_UNMANAGED`, `PATH_UNSAFE`). This is the
   "no conflict" test of the consumer contract. Otherwise stop and ask one
   question (WR-2).
3. Run it with `--apply`. If the dry run reported `PIN_OBJECT_MISSING` or
   `SOURCE_UNAVAILABLE` (the empty cache), add `--allow-network`. It fetches
   exactly the pinned commit, only from the URL recorded in the `repository` field
   of `.tooltempest.lock`, into `--source`, as `refs/tooltempest/pins/<commit>`, and
   creates the cache if it is absent. Every later read of it goes through the object
   verifier, which re-hashes the chain commit, tree and blob (docstring of
   `scripts/tooling_core/objects.py`). The Owner confirmed on 2026-11-18 that
   an agent runs this command for an empty cache. Shown by, in
   `tests/tooling/test_network.py`:
   `test_with_permission_exactly_the_pinned_commit_is_fetched_into_a_new_cache`,
   `test_the_fetch_is_for_the_pin_only_not_for_a_newer_commit`,
   `test_repository_values_that_could_run_code_or_name_other_transports_are_never_fetched`
   and `test_a_fetched_commit_is_still_verified_before_any_byte_is_used`.
4. Repeat the blocked commit or push. The state is normal when
   `python3 -B scripts/tt_pin_check.py commit --root .` exits 0.

An empty cache blocks every commit and every non-empty push of every consumer
that runs the check (fail-closed), until step 3 has run. A push that updates no
tip has nothing to check and passes.

## What the agent does not do

- It never bypasses a hook (WR-13): no `--no-verify`, no edit, removal,
  disabling or `core.hooksPath` change of a hook, no replacement of
  `scripts/tt_pin_check.py`.
- It does not edit `.tooltempest.lock`, and it does not edit an installed file by
  hand, to make a check pass. A lock edit follows the consumer contract (WR-13).
- It does not change settings, permissions or hooks, `~/.claude` included (WR-17
  states this for an instruction-only task; WR-13 forbids bypassing a hook for every task).
- It does not fetch anything except through `--allow-network` of the printed fix.

A block that no row above lets the agent fix, a block that stays after the
printed fix succeeded, and a dry run that plans `refuse` go to the Owner as one
question (WR-2). This file defines no way for an agent to get past a block that
the printed fix does not clear; "Owner emergency procedure" is the Owner's alone.

## Owner emergency procedure

Decided by the Owner on 2026-11-18. It is for the Owner only. It does not apply to
an agent and does not change any rule that forbids `--no-verify` or any bypass
for an agent (WR-13 and the rules of the consumers). An agent that meets a block
stops and asks the Owner (WR-2).

| Item | Procedure |
|---|---|
| Scope | Only when the pin check or a hook itself fails for a cause that no row of "Block messages" covers, or when the printed fix has failed twice. Never for a finding about content, such as a failing DocOps check or a secret finding: fix those, do not bypass them. |
| Who | The Owner, personally, in her own terminal. |
| What it permits | Git's own `--no-verify` for the one blocked commit or push, and nothing else: no deletion or edit of `.tooltempest.lock`, no edit, disabling or removal of a hook, no edit of an installed payload file. |
| Record | The next commit message carries the line `Pin-check-bypass: <reason> (Owner, <date>)`. The Owner also notes the bypass in the task report. |
| Return to normal | In the same working session: (1) fix the cause; (2) run the consumer's read-only `python3 -B scripts/tooling.py verify --root .`, which must exit 0; (3) run the DocOps check over the bypassed commits, `python3 -B scripts/docops.py check --tree <commit>` once per commit (the option repeats; list a range with `git rev-list <base>..HEAD`), which must exit 0; (4) the next commit and push go through the hooks without a bypass, and `python3 -B scripts/tt_pin_check.py commit --root .` exits 0. |

Not exercised: no bypass was performed to write this section, so the procedure is
a decision of the Owner and not a tested mechanism. The two checks in step (2) and
(3) are the commands of `tooling.py verify --help` and `docops.py check --help`.

## Fresh clone: hooks

Git does not transfer hooks or `core.hooksPath` with a clone. Until a person
installs them, no commit or push in the clone is checked, the pin check
included. Shown by: on 2026-11-18, `git clone` of each of the three repositories
below into an empty directory: `.git/hooks` held only `*.sample` files and
`git config --get core.hooksPath` printed nothing.

Install the pinned payload first (article-pipeline: `README.md` "Setup of a fresh clone"; brain:
`docs/runbooks/tooltempest-setup.md` section 1; radar: the command in `CLAUDE.md`
for a missing `docs/tooltempest/universal-rules.md`).
With the hooks installed and the payload absent, every commit and every non-empty
push is blocked (the first row of the table above).

| Repository | Command, from the repository root | Check that proves the installation |
|---|---|---|
| article-pipeline | `bash scripts/install-hooks.sh` | For `pre-commit` and `pre-push`: `cmp -s scripts/hooks/<hook> "$(git rev-parse --git-path hooks)/<hook>" && test -x "$(git rev-parse --git-path hooks)/<hook>"` exits 0 |
| radar | `bash scripts/install-hooks.sh` | The same command as for article-pipeline exits 0 for both hooks |
| brain | `./.githooks/install.sh` | `git config --get core.hooksPath` prints `.githooks`, and `test -x .githooks/pre-commit && test -x .githooks/pre-push` exits 0 |

Shown by: on 2026-11-18, in a scratch clone of each repository, the article-pipeline
and brain installers exited 0 and the check exited 0 for both hooks; the radar
installer, run before the payload was installed, exited non-zero with `FAIL: the
guard cannot be activated` and changed no hook. The radar installer after the
payload is installed was not run: not verified. The same check on the existing
installations of article-pipeline and radar exited 0 for both hooks. The hooks of a linked worktree
are the hooks of the main repository (`git rev-parse --git-path hooks`), so one
installation covers all worktrees of a clone.
