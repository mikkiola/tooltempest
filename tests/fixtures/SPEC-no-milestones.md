# Fixture Component Without Milestones

Fixture `SPEC.md` with no `## Milestones` section. `tests/fixtures/verify.py`
classifies it as "UNKNOWN", the pattern `doc_sync.py` blocks when a commit
touches it, and the production checker fails it (MILESTONES_MISSING).

## Goals

- [ ] a goal checkbox, which is not a milestone
