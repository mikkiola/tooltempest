---
paths:
  - "**/SPEC.md"
---

# Drift Control

3-axis drift measurement to prevent straying from goals mid-work.

## Drift tracking fields

Include drift tracking fields per milestone, wherever milestones are
tracked for the active work:

```markdown
## M1: [milestone name]
- [ ] description
- verify: `npm run typecheck && npm run test`
- done-when: 0 type errors, tests pass
- status: in-progress
- drift:
  - goal: 0.0    # goal deviation (0.0=on target, 1.0=fully off)
  - constraint: 0.0  # constraint violations (+0.1 each)
  - scope: 0.0   # scope deviation (ratio of unplanned file changes)
  - combined: 0.0  # weighted average (goal×50% + constraint×30% + scope×20%)
```

## DocOps-managed fields

`scripts/doc_sync.py` (the DocOps Protocol) no longer manages any
drift-tracking fields: CHECKPOINT.md support was retired entirely
(ADR-0010, `mikkiola/tooltempest`) once every consuming project's own
`scripts/verify.py` stopped recognizing it as a pattern. DocOps now
validates only a `SPEC.md`'s own inline `## Milestones` checklist; the
`verify:`/`done-when:`/`status:`/`drift:` fields above are tracked by
convention, not enforced or auto-filled by any tool.

## Drift thresholds

| combined | Verdict | Action |
|----------|---------|--------|
| ≤ 0.15 | ✅ Normal | Keep going |
| 0.15~0.30 | ⚠️ Caution | Check drift causes before the next task |
| > 0.30 | 🔴 Danger | **STOP** — re-plan required. Record in AUDIT.log |

## When to measure

1. **Milestone start**: initialize at 0.0
2. **After every code change**: update scope drift (unplanned files / changed files)
3. **On verification failure**: update goal drift
4. **Milestone completion**: record final drift

## Scope drift auto-calculation

```
scope_drift = (unplanned changed files) / (total changed files)
```

Unplanned file: any change to a file not listed as part of the current
milestone's planned scope

## Constraint drift accumulation

+0.1 per constraint violation:
- Performance SLA miss
- Backward-compatibility break
- Security rule violation
- Coding style violation

## Combined drift formula

```
combined = goal × 0.5 + constraint × 0.3 + scope × 0.2
```
