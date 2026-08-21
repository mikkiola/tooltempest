# Fixture Component

Minimal fixture `SPEC.md` for `tests/test-doc-sync.sh` (ADR-0005,
`docs/adr/0005-ci-pipeline.md`). `doc_sync.py` never reads this file's
content directly -- only `scripts/verify.py`'s SPEC.md/CHECKPOINT.md
pairing and CHECKPOINT.md field-presence checks matter to the scenario
suite. Present only to give the fixture's stub `verify.py` a SPEC.md to
discover.
