# ADR-0016: CI Checks for the Distributed File Set — Manifest Check and the Standard-Library Suites

## Status

Proposed, 2026-10-27. Implements the CI item of Stage 4 of the DocOps
tooling redesign. Becomes Accepted when the Owner accepts the decisions
below. It extends ADR-0005 and does not supersede or edit it: the
scenario-suite workflow that ADR defines stays exactly as it is. It
changes no repository setting and no consumer.

## Context & Constraints

**Governing source.** The Owner's instruction of 2026-10-27 names the
Stage 4 section of `stage0-contract-draft.md`
("Привести ToolTempest к релизному состоянию") as the scope authority. Its
bullet "Проверять в CI распространяемый состав файлов и применимые тесты"
and its gate ("все обязательные тесты реально исполнены и прошли; skipped
applicable tests не засчитываются") are what this record implements. The
Owner's Stage 4 prompt names the commands and the triggers (a pull request
and a push to `main`) and rules out any change to branch protection,
required status checks or other repository settings (the post-Stage-4
decision J1).

**What exists today.** ADR-0005 defines one workflow,
`docops-scenario-suite.yml`: the Tier 1 scenario script on `pull_request`
to `main`, informational, Python pinned to 3.14, with the explicit
statements that a push trigger is not wanted and that general hygiene is
out of scope and would be "a separate, later ADR". Nothing runs
`scripts/check_manifest.py`, `tests/docops`, `tests/tooling` or
`tests/distribution` automatically, although each is the evidence for a
Stage 1 to Stage 4 claim.

**Constraints.** Standard library only for the code under test. No
GitHub Actions run has been performed for this change (no pull request is
open and nothing has been pushed to `main`), and the maintainer's machine
runs macOS, not the Linux runner.

## Decision

1. **A new workflow, `.github/workflows/checks.yml`**, with four
   independent jobs on `ubuntu-latest`, each a checkout, `setup-python` and
   one command:
   - `manifest`: `python3 scripts/check_manifest.py`
   - `docops`: `python3 -B -m unittest discover -s tests/docops -t tests/docops`
   - `tooling`: `python3 -B -m unittest discover -s tests/tooling -t tests/tooling`
   - `distribution`: `python3 -B -m unittest discover -s tests/distribution -t tests/distribution`
   Permissions are `contents: read`. The commands are the ones CLAUDE.md
   documents for local runs.
2. **Triggers: `pull_request` to `main` and `push` to `main`.** ADR-0005
   chose pull-request only so that results are visible before a merge; this
   workflow adds the push because a direct push to `main` is otherwise
   unchecked, which the Owner's instruction excludes.
3. **ADR-0005's workflow is left unchanged**: still the scenario suite, still
   pull request only. This record does not alter its trigger, name, job or
   scope.
4. **Informational.** The workflow is not wired into branch protection as a
   required check, and nothing here changes a repository setting.
5. **One Python version, 3.14.** ADR-0005's exact pin is kept. The suites
   are run on Python 3.14.5 and 3.9.6 locally before each change is
   committed; CI does not cover 3.9.
6. **What the `distribution` job judges.** Its tests read the checked-out
   `HEAD` and materialize that commit. On a pull request GitHub checks out
   a merge commit of the branch into `main`, so the candidate judged there
   is the merge result; on a push it is the pushed commit.

## Alternatives & Rationale

| Option | Verdict | Why |
|---|---|---|
| Add the new steps to ADR-0005's workflow | Rejected | It would change that workflow's name, trigger and scope, which ADR-0005 fixed; a separate file leaves its history and its check names alone. |
| One job running the commands one after another | Rejected | The first failure would hide the others; four jobs give four independent signals and run in parallel. |
| A matrix of Python 3.9 and 3.14 | Deferred | ADR-0005 pins one exact version on purpose, and the Owner's instruction names none; it doubles the cost. Revisit if a consumer's hooks run on 3.9. |
| Mark the jobs as required status checks | Out of scope | The Owner defers every server-side setting to the post-Stage-4 decision. |
| A third-party action for linting or validating workflows | Rejected | An outside dependency for a four-job file; the file is validated locally instead (below). |

## Consequences

- A pull request and every push to `main` now run the manifest check and the
  three suites, so a manifest that omits a shipped file, a contract that
  drifts from its documentation, or a distribution that does not install and
  start fails visibly. Nothing blocks a merge until the Owner makes a check
  required.
- **Not yet proven:** the workflow has not been executed by GitHub Actions,
  and the suites have not been run on Linux. First failures on the runner
  may come from the platform (Git version, `/dev/fd`, file-system timestamps)
  and not from the code. A skipped applicable test would not count as a pass
  under the Stage 4 gate. Three `skipTest` calls exist (a permission test
  when run as root, and two SHA-256 tests when Git cannot create such
  repositories), and one more conditional does not skip but silently omits an
  assertion when run as root (the locked-file check in
  `tests/docops/test_impact.py`). The local runs report no skips. Whether the
  runner is non-root with a Git recent enough for SHA-256 repositories is
  expected, not established by this repository: the first runner log has to be
  read for skips and for that omission.
- Python 3.9 is covered only by the local runs recorded in each commit.
- CI minutes grow by four short jobs per pull request and per push.

## Confirmation & Revisit

Checked on 2026-10-27, locally: the workflow parses with two independent
YAML parsers (PyYAML and Ruby's Psych); the `run` commands were extracted
from the parsed file and executed unchanged from the repository root
(results recorded in the commit message of the change that adds this file);
`tests/distribution/test_workflow.py` pins, at text level, that the triggers
and the four commands are present and refer to files and directories that
exist. That test is not a YAML validation and does not execute a workflow.
Not checked: any GitHub Actions run, and any Linux run.

Revisit when the first runs on GitHub show a platform failure, when a
consumer needs Python 3.9 in CI, when the Owner decides which checks are
required, or when a suite is added that the workflow should run.

## Source

The Owner's Stage 4 instruction of 2026-10-27 and the Stage 4 section of
`stage0-contract-draft.md` it names as the authority; ADR-0005 (what exists
and what it excluded); the suites added in the preceding Stage 4 changes.
