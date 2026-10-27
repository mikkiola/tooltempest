# ToolTempest maintainer entry point

ToolTempest is the client-agnostic source for shared tooling primitives, not a
consumer installation or an adapter. Read [README](README.md) for composition,
lifecycle and scope, [docs/adr](docs/adr/) for decisions, and
[documentation rules](docs/reference/documentation-rules.md) for shared document
authority. This maintainer navigation file is not a distributable primitive.

## Source and distribution boundaries

- [MANIFEST.txt](MANIFEST.txt) is the consumer file list. Preserve its distinction
  between shared mechanisms and reference documents; reference docs are not
  installed as client rules.
- `skills/` and `rules/` contain canonical shared source. They are not this
  project's active skills/settings. Do not copy them into `.claude/` here.
- `scripts/` contains DocOps and tooling-bootstrap code and the independent
  manifest validator; `schemas/` contains protocol schemas. No
  consumer-specific integration code, account configuration or private
  runtime state belongs in this repository.
- A source change does not repin, install or activate anything for consumers.
  Preserve explicit source-selection and delivery decisions separately.

Retain the existing no-client-specific-directory boundary: add no `.claude/`,
adapter scaffolding, copied consumer workflow, project agent or duplicate shared
skill merely for uniformity. This project uses its existing README/ADR/reference
structure; do not fabricate a five-document canon or owner goal registry.

## Offline verification

For maintainer-instruction edits, check links, exact scope and `git diff --check`.
The existing independent completeness command is:

```bash
python3 scripts/check_manifest.py
```

Run it after changes to composition paths and keep the manifest consistent in
the same change. It compares tracked composition paths; an untracked file there
still needs review and inclusion before publication.

The DocOps checker (`scripts/docops.py`, `scripts/docops_core/`,
`schemas/docops-*.schema.json`) has its own standard-library test suite:

```bash
python3 -B -m unittest discover -s tests/docops -t tests/docops
```

It builds throwaway Git repositories under the system temp directory, with
hooks disabled (the no-mutation tests also install trap hooks and filters, to
show that the checker never starts them), and runs the checker against them; it
neither touches this repository's index or refs nor any consumer, and it does
not execute `doc_sync.py` hook modes. It verifies the checker's behaviour: the
SPEC-state policy and body checks, exact-snapshot reading (index, commit, pushed
refs), failure modes that must never read as PASS, the impact-report validator
(report structure, binding to the snapshot, completeness against the change,
evidence freshness), conformance of the output to the result schema, the
documented command-line contract (exit codes, `check` and `report` output,
help), a before/after proof over a defined input matrix that `check` and
`report` change nothing they inspect (its input classes and limits are stated in
the test file), and that the SPEC fixtures of the Tier 1 suite get the verdicts
that suite relies on. It does not verify that any document is correct. Run it
after changes to those paths.

The tooling bootstrap (`scripts/tooling.py`, `scripts/tooling_core/`,
`schemas/tooling-*.schema.json`) has its own suite:

```bash
python3 -B -m unittest discover -s tests/tooling -t tests/tooling
```

It builds throwaway Git repositories (a fixture ToolTempest, fixture consumers,
linked worktrees) and a throwaway HOME, hooks disabled, and runs the tool
against them; the "network" is only ever a local `file://` repository. It
neither touches this repository nor any consumer nor `~/.claude`, and it does
not run `doc_sync.py` hook modes. It verifies the tool's behaviour (verified
Git-object reads, the three reported properties, dry-run-first materialize,
refusal to overwrite, journal recovery, serialisation, the documented
command-line contract in both output formats), not that any consumer is
installed correctly. Run it after changes to those paths.

The distribution suite qualifies what consumers receive:

```bash
python3 -B -m unittest discover -s tests/distribution -t tests/distribution
```

It runs `check_manifest.py` against disposable repositories (a complete, an
incomplete and a stale manifest, and damaged variants of the real one), pins the
import surface the legacy scripts share (`relative_to_root`, `repo_root`,
`restore_snapshots`) and the behaviour its callers rely on, and materializes
the exact HEAD commit of this checkout into a disposable consumer with
`scripts/tooling.py` (the checkout's Git objects are only read; no network, no
consumer; `HOME` is pointed at a temporary directory). It then checks the complete file set against
`MANIFEST.txt`, every content hash, blob id and mode, the receipt, and that the
installed `docops.py` and `tooling.py` run and the installed legacy scripts
import. It needs a Git checkout with a HEAD commit. Run it after changes to
composition paths.

[tests/test-doc-sync.sh](tests/test-doc-sync.sh) exercises DocOps hook modes in
fixture repositories; its `verify.py` (`tests/fixtures/verify.py`) stands in for
a consumer's own script and implements only the output contract `doc_sync.py`
reads, it is not the production parser (that is tested in `tests/docops`). It is
not permitted by a no-hook-execution task; do not
run it or production `doc_sync.py` modes just to validate Markdown. Reuse prior
results for unchanged source. Shared `/verify` text is source to review here,
not authorization for a live model-backed invocation.

Keep implementation preparation separate from permission to commit/publish it.
Do not change consumer locks, global installed files, hooks, permissions or MCP
configuration during project-instruction work.
