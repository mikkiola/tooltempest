# ADR-0016: CI Checks for the Distributed File Set — Manifest Check and the Standard-Library Suites

## Status

Proposed, 2026-10-27. Implements the CI item of Stage 4 of the DocOps
tooling redesign. Becomes Accepted when the Owner accepts the decisions
below. It extends ADR-0005 and does not supersede or edit it: the
scenario-suite workflow that ADR defines stays exactly as it is. It
changes no repository setting and no consumer. Consequences and
Confirmation & Revisit were updated on 2026-10-27 with the first observed
GitHub Actions run (pull request 2); no decision was changed.

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

**What existed when this ADR was drafted (historical).** ADR-0005 defines
one workflow, `docops-scenario-suite.yml`: the Tier 1 scenario script on
`pull_request`
to `main`, informational, Python pinned to 3.14, with the explicit
statements that a push trigger is not wanted and that general hygiene is
out of scope and would be "a separate, later ADR". Nothing ran
`scripts/check_manifest.py`, `tests/docops`, `tests/tooling` or
`tests/distribution` automatically, although each is the evidence for a
Stage 1 to Stage 4 claim.

**Constraints.** Standard library only for the code under test. When this
ADR was drafted (historical), no GitHub Actions run had been performed for
this change (no pull request was open and nothing had been pushed to
`main`), and the maintainer's machine runs macOS, not the Linux runner.
The first run has since been observed; see Confirmation & Revisit.

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
- **Platform risk (written before the first run; historical).** Failures on
  the runner could have come from the platform (Git version, `/dev/fd`,
  file-system timestamps) and not from the code. A skipped applicable test
  would not count as a pass under the Stage 4 gate. Three `skipTest` calls
  exist (a permission test when run as root, and two SHA-256 tests when Git
  cannot create such repositories), and one more conditional does not skip
  but silently omits an assertion when run as root (the locked-file check in
  `tests/docops/test_impact.py`). The local runs reported no skips. The first
  run's outcome is recorded in Confirmation & Revisit.
- **Still not established:** whether the root-conditional assertion in
  `tests/docops/test_impact.py` executed on the runner (the available logs
  do not show which user the tests ran as), and anything about more than the
  one run observed.
- Python 3.9 is not covered in CI, only by the local runs recorded in each
  commit.
- CI minutes grow by four short jobs per pull request and per push.

## Confirmation & Revisit

**Before the first run (historical, 2026-10-27, local).** The workflow
parses with two independent YAML parsers (PyYAML and Ruby's Psych); the
`run` commands were extracted from the parsed file and executed unchanged
from the repository root (results recorded in the commit message of the
change that adds this file); `tests/distribution/test_workflow.py` pins, at
text level, that the triggers and the four commands are present and refer to
files and directories that exist. That test is not a YAML validation and
does not execute a workflow. At that point no GitHub Actions run and no
Linux run had been observed.

**First observed GitHub Actions run (2026-10-27).** Pull request 2,
https://github.com/mikkiola/tooltempest/pull/2, opened as a draft from
`feat/docops-checker` into `main`. Runner image `ubuntu-24.04` (what the
`ubuntu-latest` label resolved to in this run), CPython 3.14.8 (the `3.14`
pin resolved to that patch release), Git 2.55.0. Local verification used
Python 3.14.5 and 3.9.6 on macOS.

| Workflow | Job | Result | What ran |
|---|---|---|---|
| ToolTempest checks (run 40477440480) | manifest is complete | passed | `check_manifest.py`: 29 tracked files |
| ToolTempest checks | DocOps checker tests | passed | 387 tests |
| ToolTempest checks | tooling bootstrap tests | passed | 244 tests |
| ToolTempest checks | distributed file set tests | passed | 52 tests |
| DocOps Scenario Suite (run 40477440349, ADR-0005) | scenario-suite | passed | `tests/test-doc-sync.sh`, cases 1-10 |

All five jobs passed. The test counts equal those of the local runs on
Python 3.14.5.

- **Commits.** GitHub reports the PR head `7974b7a` as the run's commit, but
  every job's checkout step fetched and checked out the synthetic merge
  commit `ee096c8` ("Merge 7974b7a into 52a9288", `refs/remotes/pull/2/merge`).
  The suites, including `distribution`, therefore ran against that merge
  result, not against `7974b7a` itself (Decision 6).
- **Skips.** No skipped test was reported: the three unittest summaries read
  plain `OK`, where unittest prints `OK (skipped=N)` when a test is skipped.
- **Not established.** The conditional assertion in
  `tests/docops/test_impact.py` that is silently omitted when run as root
  does not report a skip, and the available logs do not show which user the
  tests ran as, so whether it executed is unconfirmed. Python 3.9 is not
  covered in CI. Only one run has been observed, so nothing is known about
  repeatability.
- The runs produced a deprecation warning about the Node.js 20 runtime of
  `actions/checkout@v4` and `actions/setup-python@v5`; it did not affect the
  results and no change was made for it.

Revisit when a later run on GitHub shows a platform failure or a skip, when
a consumer needs Python 3.9 in CI, when the Owner decides which checks are
required, or when a suite is added that the workflow should run.

## Source

The Owner's Stage 4 instruction of 2026-10-27 and the Stage 4 section of
`stage0-contract-draft.md` it names as the authority; ADR-0005 (what exists
and what it excluded); the suites added in the preceding Stage 4 changes.
