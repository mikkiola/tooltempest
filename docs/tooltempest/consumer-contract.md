# ToolTempest Consumer Contract

**ToolTempest consumer obligation** — mandatory for every project that
consumes `mikkiola/tooltempest`. This file is its single source; a project
links to it and does not copy it. Whenever a session works on ToolTempest
itself and adds/removes/renames a file under `scripts/`, `schemas/`,
`skills/`, `rules/`, `docs/reference/` or `docs/tooltempest/` (the six
directories `scripts/check_manifest.py` compares with `MANIFEST.txt`), that
session must run that script and update `MANIFEST.txt` in the same commit if
it reports a mismatch. Repinning
`.tooltempest.lock` to pick up the change is a separate, deliberate
action, never automatic. "Never automatic" means that no tool
repins implicitly. It does not forbid an explicit repin by an agent,
which is allowed when the evidence shows no behavior change for the
consumer (the paragraph below).

A change to ToolTempest source does not repin, install, or activate anything for a consumer.

Updating a consumer's pin (a repin) is a routine step within a task. A task
prompt that names a repin authorizes the change of `.tooltempest.lock` and the
local commit of that change (the Owner's decisions of 2026-10-30 and
2026-11-11), when all of these hold:

- The change is limited to the repin. The lock file shows no unrelated change.
- The consumer's checks pass: the checks that its own instructions prescribe for a commit.
- The evidence shows that the consumer's behavior does not change. The report
  states for each piece of evidence what it proves and what it does not. Passing
  tests alone are not proof of no consequence.

The repin evidence is per component. Only core (`scripts/`, `schemas/`) has an evidence set that can show an unchanged behavior; it is:
the checker of the new commit gives the same verdicts on the consumer's
committed tree; the payload paths that differ are within the manifest and the
materialize dry run shows no conflict; and `tooling.py verify` exits 0.

Stop and ask the Owner when the evidence is insufficient, the consumer's
behavior changed, the lock file shows unrelated changes, or the repin carries a
change to the contract (a file under `docs/tooltempest/`) or to the client
(`skills/`, `rules/`) that the Owner has not decided. An Owner decision to
change the contract or the rules authorizes the repin itself, the checks, and a
local commit of the result. It does not authorize publication. The repin does
not include `git push`; every push follows WR-14 of the universal rules. Every
repin is reported with this evidence. A repin never runs automatically because
ToolTempest changed; it is an explicit step of a task.

In that paragraph, `<current>` is the commit the consumer's
`.tooltempest.lock` pins now, `<new>` is the commit it would pin, and
`<new-checkout>` is a ToolTempest checkout that holds `<new>`:
- **The checker of the new commit** is `scripts/docops.py` as it is at
  `<new>`, run on the consumer's committed tree:
  `python3 <new-checkout>/scripts/docops.py check --repo <consumer>
  --tree HEAD`. It gives the same verdicts as the checker of
  `<current>` when the same command, run from a checkout of
  `<current>` taken from the ToolTempest repository, returns the same
  `summary.status` and `summary.exit_code` and the same `status` for
  every check under `snapshots[].checks` that has one.
- **The payload paths** are the paths under `scripts/`, `schemas/`,
  `skills/`, `rules/` and `docs/reference/` in the tree of `<current>` or of
  `<new>`; no other path is payload, and the contract root `docs/tooltempest/`
  is not payload. The ones
  that differ are the output of `git diff --no-renames
  --name-only <current> <new> -- scripts/ schemas/ skills/ rules/
  docs/reference/`. They are within the manifest when each one is a
  line of `MANIFEST.txt` at `<new>` (`git show <new>:MANIFEST.txt`).
- **The materialize dry run** is `python3
  <new-checkout>/scripts/tooling.py materialize --root <consumer>
  --lock <lock-copy> --source <new-checkout>`, without `--apply` and
  without `--allow-network`, where `<lock-copy>` is a copy of the
  consumer's `.tooltempest.lock`, kept outside the consumer's tree,
  that differs from it only in `commit`, which is set to `<new>`. It
  shows no conflict when it exits 0 and plans no file as `refuse`
  (`CONFLICT_MANAGED_MODIFIED`, `CONFLICT_UNMANAGED` or
  `PATH_UNSAFE`).
