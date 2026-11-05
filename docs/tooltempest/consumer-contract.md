# ToolTempest Consumer Contract

**ToolTempest consumer obligation** — mandatory, copied verbatim (or
near-verbatim) into every project consuming `mikkiola/tooltempest`.
States: whenever a session works on ToolTempest itself and
adds/removes/renames a file under `scripts/`, `schemas/`, `skills/`,
or `rules/`, that session must run ToolTempest's completeness-check
script (`scripts/check_manifest.py`) and update `MANIFEST.txt` in
the same commit if it reports a mismatch. Repinning
`.tooltempest.lock` to pick up the change is a separate, deliberate
action, never automatic. "Never automatic" means that no tool
repins implicitly. It does not forbid an explicit repin by an agent,
which is allowed when the evidence shows no behavior change for the
consumer (the paragraph below).

Updating a consumer's pin (a repin) is a routine autonomous step within a
task. The repin evidence is per component. For core (`scripts/`, `schemas/`)
it needs no separate Owner decision when the evidence shows no behavior change
for the consumer: the checker of the new commit gives the same verdicts on the
consumer's committed tree; the payload paths that differ are within the
manifest and the materialize dry run shows no conflict; and `tooling.py
verify` exits 0. A change to the contract (this file) or to the client
(`skills/`, `rules/`) needs the Owner's approval of the diff. Otherwise the
repin needs an Owner decision. Every repin is reported with this evidence. A
repin never runs automatically because ToolTempest changed; it is an explicit
step of a task.

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
  `skills/`, `rules/` and `docs/reference/` (the five directories
  `scripts/check_manifest.py` compares with `MANIFEST.txt`) in the
  tree of `<current>` or of `<new>`; no other path is payload. The
  ones that differ are the output of `git diff --no-renames
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

_Dated note (2026-11-03, repin policy): the paragraph that starts
"Updating a consumer's pin" states when a repin needs an Owner
decision. The "never automatic" sentence says what it means, and the
list above defines the three terms of the evidence test; the repin
rule itself is unchanged._

_Dated note (2026-11-04, consumer contract): this file is the text of item 7
of the Constitution section of `docs/reference/documentation-rules.md`, moved
here verbatim so that it has one source (ADR-0017). Only the paragraph that
starts "Updating a consumer's pin" was rewritten, to state the repin evidence
per component that ADR-0017 decided: the evidence for core is the evidence the
paragraph stated before, and a change to the contract or to the client needs the
Owner's approval of the diff. The note of 2026-11-03 above describes the
paragraph as it was before that rewrite. `scripts/check_manifest.py` now
compares six directories: the five named in the definition of the payload paths
and `docs/tooltempest/`, the directory of this file; the payload paths defined
above are unchanged._
