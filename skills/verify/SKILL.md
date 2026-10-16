---
name: verify
description: "Code verification after task completion (typecheck, lint, test, build). Triggers on: verify, run tests, check build, typecheck. NOT for: E2E tests, code writing, implementation."
user-invocable: true
disable-model-invocation: false
allowed-tools: Read, Bash, Grep, Edit
---

# Code Verification

> This is a prompt-based verification procedure. Run it for manual requests or through a verified integration; do not assume an automatic hook ran it.

Verifies code after task completion. Run applicable checks in order; do not proceed past an unresolved required-check failure. Report later unexecuted checks as SKIP.

## Step 1: Project Detection

| File | Stack | Typecheck | Lint | Test | Build |
|------|-------|-----------|------|------|-------|
| `package.json` | Node/TS | `npx tsc --noEmit` | `npx eslint .` / `npx biome check .` | `npx vitest run` / `npx jest` | `npm run build` |
| `pyproject.toml` | Python | `mypy .` / `pyright .` | `ruff check .` | `pytest -q` | — |
| `go.mod` | Go | `go vet ./...` | `golangci-lint run` | `go test ./...` | `go build ./...` |
| `Cargo.toml` | Rust | — | `cargo clippy` | `cargo test` | `cargo build` |

```bash
# Auto-detect
[ -f package.json ] && cat package.json | python3 -c "import sys,json; s=json.load(sys.stdin).get('scripts',{}); print('\n'.join(f'{k}: {v}' for k,v in s.items()))"
[ -f pyproject.toml ] && echo "Python project detected"
[ -f go.mod ] && echo "Go project detected"
[ -f Cargo.toml ] && echo "Rust project detected"
```

### Select checks and retain their evidence

Use the task/specification and project configuration to select applicable checks; the table lists examples, not a universal coverage requirement. Discovery alone is not verification, and the discovery block's exit code is not a check verdict. Mark a category N/A only with evidence that it is outside the applicable scope; a missing tool or unsupported category is SKIP, not N/A.

Run each selected command and each underlying suite independently. Choose applicable alternatives from the examples rather than executing every stack's command. Record each actual exit code and diagnostic immediately; a later command, retry, summary line, or successful outer wrapper must not overwrite an unresolved underlying failure. A retry clears only the same check's failure after a successful rerun against the current state. If a wrapper does not expose its underlying results, report that verification as SKIP (unverified).

For tests, record executed/pass/fail/skip counts from the runner when available. For an applicable test check, zero tests executed is SKIP, not PASS, even if the runner exits 0. Unexecuted applicable tests are coverage gaps: report partial execution as SKIP with the passed/failed/skipped counts. Missing, ambiguous, or invalid result evidence is SKIP (unverified), never PASS.

## Step 2: Verification Order (fix immediately on failure)

### 2-1. TypeScript Typecheck
```bash
npx tsc --noEmit
```
On failure: fix type errors → re-run. After 3 failures, escalate to the user.

### 2-2. Lint
```bash
# ESLint
npx eslint . --max-warnings=0
# or Biome
npx biome check .
# Python
ruff check .
```
On failure: try `--fix` auto-fix → manual fix → re-run.

### 2-3. Tests
```bash
# Vitest
npx vitest run
# Jest
npx jest
# pytest
pytest -q
```
On failure: analyze failing tests → fix the code (modifying tests is a last resort) → re-run.

### 2-4. Build
```bash
npm run build
```
On failure: analyze build errors → fix → re-run.

### 2-5. Circular Dependency Detection (JS/TS projects)
```bash
npx madge --circular --extensions ts,tsx src/
```
If circular dependencies are found: report as WARNING. Only newly introduced cycles are fix targets.

### 2-6. Dead Code Detection (JS/TS projects)
```bash
npx knip --no-exit-code
```
If unused exports/files/dependencies are found: report as INFO. Do not auto-fix.

### 2-7. Secret Scan
```bash
gitleaks detect --source . --no-git -v 2>&1
```
If secrets are found: CRITICAL. Report immediately and drive a fix. PASS requires a completed scan with exit code 0 and no findings. A scanner execution failure is FAIL; retain its actual exit code and diagnostic.

### 2-8. AI Slop Pattern Detection (ast-grep)
Run the two pattern checks separately and retain each result.

```bash
# Unnecessary console.log
sg --pattern 'console.log($$$)' --lang ts src/
```

```bash
# any type usage
sg --pattern '$A as any' --lang ts src/
```
If found: report as WARNING. Distinguish documented finding/no-match exits from tool execution errors using diagnostics; findings remain advisory, but a tool execution error is FAIL. Apply the same distinction to circular and dead-code checks. An ambiguous outcome is SKIP (unverified).

## Step 3: Result Report

| Step | Status | Details |
|------|--------|---------|
| typecheck | PASS/FAIL/SKIP/N/A | Command, actual exit, diagnostic or gap/exclusion reason |
| lint | PASS/FAIL/SKIP/N/A | Command, actual exit, warning/error count or gap/exclusion reason |
| test | PASS/FAIL/SKIP/N/A | Command, actual exit, executed/passed/failed/skipped counts or gap/exclusion reason |
| build | PASS/FAIL/SKIP/N/A | Command, actual exit, diagnostic or gap/exclusion reason |
| circular | PASS/WARN/FAIL/SKIP/N/A | Command, actual exit, cycle count or diagnostic/gap/exclusion reason |
| deadcode | INFO/FAIL/SKIP/N/A | Command, actual exit, unused export/file count or diagnostic/gap/exclusion reason |
| secrets | PASS/CRITICAL/FAIL/SKIP/N/A | Command, actual exit, finding count or diagnostic/gap/exclusion reason |
| ai-slop | PASS/WARN/FAIL/SKIP/N/A | Each command and actual exit, finding counts or diagnostic/gap/exclusion reason |

**Individual results:** PASS requires executed, validated success; a test check with zero applicable tests executed cannot PASS, and missing underlying results cannot establish PASS for any check. A known failed check is FAIL while unresolved, including before retries are exhausted. SKIP means not run, unsupported, partially executed, or unverified; always state which and why. N/A is an evidenced scope exclusion. Confirmed advisory findings retain WARN/INFO.

**Overall verdict (in this order):**
1. Any unresolved FAIL or CRITICAL → FAIL.
2. Otherwise, any applicable SKIP or missing/ambiguous/invalid result, no relevant checks executed, or an applicable test check executed zero tests → INCOMPLETE.
3. Otherwise → PASS for the explicitly listed verified scope only. WARN/INFO may coexist with PASS when they represent validated advisory findings. N/A exclusions do not count as executed or passed checks.

State the verified scope and all coverage gaps/exclusions. Do not claim full project verification from a subset, or infer coverage from command presence or a summary line. This skill produces a report, not its own executable exit code; report measured command/suite exits separately. If an integration maps this report to an exit status, FAIL and INCOMPLETE must map to nonzero; verify that mapping rather than assuming an outer exit 0 proves success.

## Fix Strategy by Error Category

| Category | Priority | Fix Method |
|----------|----------|------------|
| TYPE | High | Extract file:line from output → Read the file → fix the type |
| LINT | Medium | `--fix` auto-fix first → manual fix |
| TEST | High | Analyze failing tests → fix the code (modifying tests is a last resort) |
| BUILD | High | Analyze build logs → check import/export issues first |

## Max Retries
Up to 3 attempts per step. After 3 failures, stop and report to the user.
