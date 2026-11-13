# ADR-0023: A Commit or Push Check Requires an Impact Report When Code Changed

## Status

Accepted, 2026-11-13. The Owner's decision of that day: the impact signal goes to the program, not to the Owner. It supersedes only the parts of Decision 8 and Decision 9 of ADR-0014 that say a run without a report leaves `DOC_IMPACT_ASSESSMENT` "not assessed" and passes, and only for the commit check and the push check. ADR-0014 stays as it is.

## Context

Under ADR-0014 the checker validates an impact report only when `--impact-file` is given. Without it the dimension reads "not assessed" and the run exits 0, so an agent can commit and push changed code with every check green and no assessment of what the change affects. Nobody is told that the assessment is open. The Owner decided that the signal is addressed to the program that runs the action: the executing agent receives an instruction and acts on it.

The checker has no classification of code paths and document paths. Its only path classification is the SPEC one (ADR-0013). The canonical documents of the universal rules are all Markdown files. The report format, the profiles, the snapshot binding and the staleness checks of ADR-0014 are reused unchanged.

## Decision

1. **Scope.** The rule applies to the commit check (`--tree index`, the index against the tree of `HEAD`) and to the push check (`--push-stdin`, each pushed ref against its base). The base of a pushed ref is the tree of the old tip that Git passes on stdin. A new ref has no old tip: its base is the parent of its oldest commit that no remote-tracking ref holds (the empty tree for a root commit; a tip that a remote-tracking ref already holds has no change). A run with `--tree REV`, a deleted ref and a run with `--impact-file` behave as before.
2. **Code path.** A changed path is a document if its name ends in `.md` (any case) and a code path otherwise. A path that is not valid UTF-8 or holds a control character cannot be classified.
3. **Signal.** If at least one code path changed and no report exists for the exact change, `DOC_IMPACT_ASSESSMENT` is FAIL with the reason code `IMPACT_REPORT_REQUIRED` and the run exits 1. Its message is a command to the executing agent. It lists the changed code paths, tells the agent to assess the effect on the eight categories of ADR-0014, to update the affected documents or record the reason, to write the report (the minimal profile is enough for an ordinary change) to the path it names, and to repeat the action. A change without a code path produces no signal.
4. **Where the report is.** The report is not a tracked file. The checker reads `<git common dir>/docops-impact/<base tree>_<subject tree>.json`. The name binds the report to both ends of the change, and ADR-0014 still binds it to the subject tree and the base commit and checks the freshness of its evidence. A report that exists is validated as before and its status is the status of the dimension. Another change of the index gives another subject tree, so an old report is never reused for it.
5. **Failures never read as "no signal".** An unclassifiable path without a report is FAIL with `IMPACT_CLASSIFICATION_FAILED`, and an index with unmerged entries, which has no tree to bind a report to, is FAIL with `IMPACT_SUBJECT_UNAVAILABLE`. A base that is not in the repository and an unreadable report are tool errors (exit 2) with their own codes.

## Consequences

- The behaviour reaches a consumer only through its own repin. Its hooks keep their current behaviour until then, and ToolTempest itself has no hook that runs the checker.
- A commit or push of code now fails until a report for that exact change exists. The cost is one report per change; the minimal profile keeps it small. A docs-only change that passed before passes now.
- The new check proves that a report exists, is bound to the change, covers every changed path and is current. It does not prove that the assessment is right (ADR-0014).
- The result schema is unchanged. The signal is a FAIL of the dimension with a zero-byte report entry that names the path where the report was looked for. `TOOL_VERSION` becomes 0.4.0.
- The classification is deliberately coarse: any non-Markdown file, including configuration and schemas, is a code path, because those changes affect interfaces and configuration. A Markdown file is never a code path, even if a tool reads it.
- The decision of each row is a pure function over a finite set of inputs, and the tests cover every combination of mode, classification, code change and report state.
