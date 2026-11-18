# Steps After a ToolTempest Publication

| Field | Value |
|---|---|
| Type | Reference |
| Reader | The Owner and an agent that carries out a publication task for ToolTempest |
| Source | `docs/tooltempest/consumer-contract.md` (repin), `scripts/tt_pin_check.py` and the user-level gate `docs_gate_v2.py` (the cache and the vendored verifier) |
| Delivery | Not installed (ADR-0008). Read in this repository. |

This file adds no rule. It collects, in one order, the steps that a push to
`origin/main` of ToolTempest leaves undone on the machine and in the consumers.
Nothing in this list runs by itself: a publication installs, repins and activates
nothing (`docs/tooltempest/consumer-contract.md`). `<new>` is the full hash of the
published commit, `<old>` the previous published commit.

## Steps, in order

Level: **local** is the user's own clone or cache under `~/Dev` and `~/.cache`
and needs only the task that names it. **machine** is a file under `~/.claude`;
it needs the Owner's approval of that change, and an agent edits it only when the
task prompt names that change (WR-13 of `docs/tooltempest/universal-rules.md`). **consumer** is a
consumer repository.

1. **Fast-forward the local ToolTempest clone** (local).
   `git -C ~/Dev/github.com/mikkiola/tooltempest fetch origin`, then
   `git -C ~/Dev/github.com/mikkiola/tooltempest merge --ff-only origin/main`.
   Shown by: `git -C ~/Dev/github.com/mikkiola/tooltempest rev-parse HEAD` prints
   `<new>`. The merge refuses when the clone has diverged; then stop and ask (WR-2).
   Step 3 reads this clone, so this step comes first.
2. **Fetch into the cache** (local). `git -C ~/.cache/tooltempest/repo fetch origin`.
   The cache is a clone whose remote is `origin` (`git -C ~/.cache/tooltempest/repo remote -v`);
   the pin check and the user-level gate read its Git objects, never its working
   tree. Shown by: `git -C ~/.cache/tooltempest/repo cat-file -e <new>^{commit}`
   exits 0. A consumer that pins a commit that the cache lacks is blocked at its
   next commit or push: see "Empty cache".
3. **Only when `scripts/tooling_core/objects.py` changed: update the vendored
   verifier** (machine, Owner-approved). It changed when
   `git -C ~/Dev/github.com/mikkiola/tooltempest diff --name-only <old> <new> -- scripts/tooling_core/objects.py`
   prints the path. When it prints nothing, skip a to d.
   a. Save `git -C ~/Dev/github.com/mikkiola/tooltempest show <new>:scripts/tooling_core/objects.py`
      as `~/.claude/hooks/vendor/objects_<first 8 hex of its sha256>.py`. Keep the
      previous file there; removing it is a separate decision.
   b. Set `VENDOR_NAME` and `VENDOR_SHA256` in `~/.claude/hooks/docs_gate_v2.py` to that
      file name and the full sha256 (`shasum -a 256` of the file).
   c. Run the tests of the hooks: `python3 -B -m unittest discover -s ~/.claude/hooks/tests`.
   d. Run `python3 ~/.claude/hooks/docs_gate_v2.py --drift`. It compares the vendored
      verifier with `objects.py` at the `HEAD` of the local clone (step 1), prints
      `same` and exits 0, or prints `DRIFT` and exits 1.
   Step 4 follows step 3.
4. **Repin each consumer** (consumer): every repository under `~/Dev/github.com/mikkiola`
   that holds a `.tooltempest.lock` (on 2026-11-18: article-pipeline, brain, radar;
   shown by `git -C <repo> cat-file -e HEAD:.tooltempest.lock`). A task prompt
   that names the repin authorizes the lock change and the local commit, under the
   conditions and with the evidence of `docs/tooltempest/consumer-contract.md`. The
   push is a separate permission (WR-14). The pin is the full hash `<new>`; the
   repin evidence names, per consumer, the checker verdicts, the payload paths and
   the materialize dry run.

## Empty cache

Fact: a commit or a push in a consumer whose lock pins a commit that the cache
does not hold is blocked by the pin check (fail-closed). The same holds when the
cache directory is absent or is not a clone. Shown by: on 2026-11-18,
`python3 -B scripts/tt_pin_check.py commit --root <consumer> --source <dir>` and the
same with `push` exited 1 for `<dir>` an absent path, an empty directory and an
empty Git repository, and exited 0 with the default cache.

Fix: step 2 above when the cache is a clone that lacks the commit. When the cache
is absent or is not a clone, only the fix that the block message prints works (see
`docs/reference/pin-check-recovery.md`, "Run the printed fix"); it adds `--allow-network`.

## Git versions

The suites of `CLAUDE.md` (`tests/docops`, `tests/tooling`, `tests/mutable_reads`,
`tests/distribution`) and `scripts/check_manifest.py` were run on 2026-11-18, on the
tree that added this file, once per Git version, with that Git first in `PATH`:

| Version | Where | Result and what shows it |
|---|---|---|
| 2.44.0 | `/opt/homebrew/bin/git`, first in `PATH` on the Owner's machine | `git --version`; 459, 324, 37 and 78 tests, each suite `OK` with no skip |
| 2.54.0 | `/usr/bin/git` (Apple Git-157) on the same machine | `/usr/bin/git --version`; 459, 324, 37 and 78 tests, each suite `OK` with no skip |

Stated by the Owner and not verified here: CI ran on 2.56.1. `.github/workflows/checks.yml`
runs `ubuntu-latest` and pins no Git version, so the version changes without a commit.

Other versions are untested. No Git version check exists: `git grep -n -e "--version" -- scripts`
finds only the version option of `tooling.py`, not a check of Git. Behavior that
depends on the version is known for lazy fetch only (the docstrings of
`scripts/tooling_core/objects.py` and `scripts/docops_core/gitstate.py`: on 2.44.0
`GIT_NO_LAZY_FETCH=1` alone does not stop a fetch, `GIT_ALLOW_PROTOCOL=none` alone
does; on 2.54.0 each alone does), and both variables are set on purpose.
