# Changelog

## 2026-08-18

- Composition bumped V1 → V2 (ADR-0001, `docs/adr/0001-docops-protocol.md`):
  added the DocOps Protocol.
  - `scripts/doc_sync.py` — the first executable code in ToolTempest;
    invoked from a consuming project's `.git/hooks/pre-commit` and
    `.git/hooks/pre-push` to keep `SPEC.md`/`CHECKPOINT.md` structurally
    consistent at commit time (structural-only reconciliation, no AI
    call) and to hard-validate at push time.
  - `schemas/execution-record.schema.json` — schema for the
    `.tempest/runs/docops_<run_id>.json` audit record `doc_sync.py`
    writes on a successful pre-commit run. Deliberately has no
    `commit_sha` field; see ADR-0001.
  - `skills/doc-sync/SKILL.md` — reference material for the protocol
    and its audit records.
  - `rules/drift-control.md` updated with a note on the `TODO`
    placeholder DocOps writes for a missing CHECKPOINT field.
- Source of this decision: `mikkiola/article-pipeline` owner request
  specifying the DocOps Protocol's ten invariants; two open questions
  (the RECONCILE signal source, and this repository's V1 "exactly three
  files" scope boundary) were raised back to the owner rather than
  resolved by assumption — see ADR-0001 "Source".

## 2026-08-14

- Repository created. Migrated the three shared tooling primitives
  from a local `~/.claude/` installation (source: article-pipeline dev
  environment) into this canonical, client-agnostic repository:
  - `skills/spec/SKILL.md`
  - `skills/verify/SKILL.md`
  - `rules/drift-control.md`
- Source of the decisions behind this split: Article Pipeline
  CAUSAL_MEMORY_ap_260813-2 (D-026, D-027) and
  CAUSAL_MEMORY_ap_260813-3 (D-028).
- Not included in this commit: a CLI/client adapter, a discovery or
  update mechanism, and a `.tooltempest.lock` file in any consuming
  project. This commit establishes the canonical three-file layer
  only.
