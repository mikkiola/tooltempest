# Changelog

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
