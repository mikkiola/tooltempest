# ADR-0017: Distribution Model for DocOps — Components, Component Fingerprint, Fingerprint Freshness, Delivery and Receipt Format

## Status

Proposed, 2026-11-04. Records the Owner's decisions D1 to D11 of 2026-11-04
as Decisions 1 to 11, in that order, and no others. Nothing in this record
changes a hook, a consumer or a lock.

Relation to earlier records (this repository's ADRs carry no metadata block,
so relations are stated here, as in ADR-0010 and ADR-0016):

- **Extends** ADR-0015 (tooling bootstrap). Where a statement of ADR-0015
  cannot be satisfied together with the Decisions below, this record
  prevails (Decision 11). ADR-0015 is not edited.
- **Relates to** ADR-0006 (manifest), ADR-0008 (reference documents),
  ADR-0013 and ADR-0014 (the checker, part of the core component), ADR-0016
  (the distribution suite that pins the delivered file set). None is
  superseded or edited.

## Context & Constraints

**Governing source.** The Owner's decisions of 2026-11-04 (D1 to D11), given
in the instruction that asked for this record.

**Observations the decisions depend on.** Each was taken at ToolTempest commit
`6510b7554f4c406d3603b8f9792396a3c766bb4d` unless another source is named, and
can be repeated with the command or location given.

1. *Content reaches a consumer by two installers.* `scripts/tooling.py
   materialize` installs the manifest entries under `scripts/` and `schemas/`
   (`parse_manifest` in `scripts/tooling_core/declarations.py` maps
   `INSTALL_ROOTS` to install, `GLOBAL_ROOTS` and `docs/reference/` to
   skipped) and writes a per-checkout receipt. Each consumer also keeps its own
   `scripts/sync-tooling.sh`, which copies the manifest's `skills/` and
   `rules/` entries into `~/.claude` and copies `scripts/` and `schemas/` into
   the repository. It reads the pinned commit from `.tooltempest.lock`; it
   writes neither the lock nor a receipt and does not call `tooling.py`. Tests
   of that script are kept in each consumer (`git ls-files | grep -E
   'sync[-_]tooling'` in each consumer). Observation from the Owner-held
   consumer audit of 2026-11-03, an audit that this repository does not
   contain.
2. *The materializer has no notion of Git-ignored files.* `grep -n -i ignore
   scripts/tooling_core/engine.py scripts/tooling_core/declarations.py
   scripts/tooling_core/fsops.py` finds no use of ignore rules. Whether an
   installed file shows in `git status` is decided by the consumer's
   `.gitignore`, which in the audited consumers lists each vendored file or
   directory by name.
3. *The destination set is fixed in three places.* `INSTALL_ROOTS =
   ("scripts", "schemas")` (`declarations.py`), the receipt parser's check
   that a path's first component is in `INSTALL_ROOTS` (`parse_receipt`), and
   the receipt schema's path pattern `^(scripts|schemas)/.+` (twice in
   `schemas/tooling-receipt.schema.json`). A manifest line under any other
   root is `MANIFEST_MALFORMED`. `tests/tooling/test_formats.py` pins that a
   receipt path outside the install roots is rejected.
4. *The root, lock and receipt are already arguments, and the existing
   in-repository install lock is created without a link check.* `--root`,
   `--lock` and `--receipt` exist (`scripts/tooling_core/cli.py`). Read at the
   commit above: `_acquire_lock` in `engine.py` runs `git
   rev-parse --absolute-git-dir` in the root (a root that is not a Git
   checkout fails with `NOT_A_REPOSITORY`), opens `tooltempest-install.lock`
   in that directory with `os.open(path, os.O_RDWR | os.O_CREAT, 0o600)` (no
   `O_EXCL`, no `O_NOFOLLOW`) and takes `fcntl.flock(LOCK_EX | LOCK_NB)`; a
   lock already held means the run is busy. `fsops.py`, which the installation
   writes use, does use `O_NOFOLLOW` and `O_EXCL`; the lock does not go
   through it.
5. *A file that left the manifest stays on disk and raises no finding.* When
   the pinned manifest is known, `_build_items` builds the items from its
   install list only, and `_final_receipt` writes a receipt from those items
   only. `tests/tooling/test_materialize.py`
   (`test_a_file_dropped_from_the_manifest_is_left_in_place_and_leaves_the_receipt`)
   asserts "nothing is ever deleted" and that the record leaves the receipt.
   No finding names such a file. One exception: a path for which the lock
   declares a hash and which the pinned manifest does not install gets the
   error `DECLARED_PATH_NOT_INSTALLABLE`, which refuses the whole plan
   (`test_a_lock_declaring_a_path_the_manifest_does_not_install_is_refused`).
   Verified from the code and those tests. Two separate facts about the
   history of `MANIFEST.txt`:
   - (1) `git log -p --format= -- MANIFEST.txt | grep '^-[^-]'` prints one
     line, `-docs/reference/canonical-documentation-bible.md`, removed by
     commit `a59d9aa249a0be68e8ed329f857cb77971fd54af`. It was a
     reference-only entry: the materializer skips `docs/reference/` and
     consumers never install it.
   - (2) `git log -p --format= -- MANIFEST.txt | grep -c -E
     '^-(scripts|schemas|docs/tooltempest|skills|rules)/'` prints 0: no path
     of an installed root has been removed from the manifest so far.
6. *Freshness is commit distance.* `_freshness` in `engine.py` compares the
   pin with the tip of a local upstream ref by ancestry and `rev-list --count`
   (statuses `current`, `behind`, `not_on_ref`, `unknown`);
   `tests/tooling/test_verify.py` pins them. It never changes the exit code.
7. *Hooks reach the network.* Both consumers' pre-push hooks run `git
   ls-remote <ToolTempest> main` and compare the SHA to the pin as strings
   (`grep -n ls-remote scripts/hooks/pre-push` in article-pipeline,
   `.githooks/pre-push` in the Brain worktree). The checker the hooks call
   (`scripts/docops.py`) states that it never uses the network (README). The
   hook lines are an observation from the Owner-held consumer audit of
   2026-11-03, an audit that this repository does not contain; the consumers
   were not re-read for this record.
8. *Copies of the consumer obligation are not compared.* Item 7 of
   `docs/reference/documentation-rules.md` is "copied verbatim (or
   near-verbatim)" into each consumer's Constitution; no check in this
   repository compares those copies. The copying is an observation from the
   Owner-held consumer audit of 2026-11-03, an audit that this repository does
   not contain.
9. *The command that finds changed paths is already documented.* Item 7 of
   `docs/reference/documentation-rules.md` defines the differing payload paths
   as the output of `git diff --no-renames --name-only <current> <new> --
   scripts/ schemas/ skills/ rules/ docs/reference/`. Run on a scratch
   repository with Git 2.44.0 (the scratch output is not kept in the
   repository), `git diff --no-renames --raw` reports a rename as one removal
   plus one addition and a mode-only change as `M` with equal object ids and
   different modes.
10. *Git tree fields are available.* `docops_core/gitstate.py` (`Entry`: mode,
    oid, stage, path; `list_tree`) and `tooling_core/objects.py` (`TreeEntry`:
    mode, oid; `ObjectStore.resolve`) already read them from trees.
11. *Client independence in the README.* The README commits that the
    repository "does not and will not contain `.claude/`, `.cursor/`, or any
    other client-specific directory", states that delivery of skills and rules
    to a client is "the job of that separate client adapter", and that
    ToolTempest "delivers nothing to a client directory".
12. *The receipt parser and schema are closed.* `parse_receipt` in
    `declarations.py` requires the exact field set (`_RECEIPT_KEYS`; per file
    `_RECEIPT_FILE_KEYS`, plus the two `previous_*` fields in the
    `installing` state), requires `schema_version` to equal
    `RECEIPT_SCHEMA_VERSION` (1) and rejects any other value as
    `RECEIPT_MALFORMED`; `schemas/tooling-receipt.schema.json` is closed
    (`additionalProperties: false`, `schema_version` const 1). A receipt or
    journal written by a newer version therefore cannot be read by this one.

**Constraints.** Standard library only; verification stays offline; no
consumer, hook, global configuration or `~/.claude` is changed by building
this record, and tests use temporary directories and a temporary HOME; the
lock format does not change; the fingerprint stores no new field; no engine
code is built for the fingerprint or for freshness; a source change repins
nothing (CLAUDE.md of this repository).

## Decision

1. **Three components (D1).** Distributable content is divided into
   components, each with its own change test (Decision 2), freshness
   (Decision 3) and repin criterion (Decision 7):
   - **core**: the materialized roots `scripts/` and `schemas/`;
   - **contract**: the consumer-facing obligations only, extracted from item 7
     of the documentation rules into one consumer-contract file that is listed
     in `MANIFEST.txt` and lives in its own root, `docs/tooltempest/`, under
     the docs area and separate from `docs/reference/`. The root name is
     chosen here; the file name is chosen when the file is written. The file
     holds what a consumer must do and nothing that governs ToolTempest's own
     documents. `docs/reference/documentation-rules.md` stays internal: it is
     not part of the contract and no component delivers it;
   - **client**: `skills/` and `rules/`.
   The three components are disjoint. A path outside them, including
   `docs/reference/`, is not fingerprinted.

2. **Component fingerprint (D2), a specification.**
   - *Definition.* The fingerprint of a component at a commit is the list of
     every Git tree entry of that commit under the component's paths, as
     records of (path, mode, Git object ID), ordered by path in byte order. Its
     text form is one line per entry, `<mode> <object id>`, a tab, the path as
     raw bytes, and a newline; `git ls-tree -r -z --full-tree <commit> --
     <component paths>` yields these fields. A component with no entry has the
     empty list, which is different from unknown. Two fingerprints are equal
     if and only if their lists are byte-identical.
   - *Computed on demand from two snapshots.* Nothing is stored in the lock,
     the receipt or any file; the lock format does not change.
   - *The documented command computes the difference.* The set of paths that
     differ between a snapshot `<current>` and a snapshot `<new>` is the
     output of the command already documented in item 7 of the documentation
     rules, restricted to the component's paths: `git diff --no-renames
     --name-only <current> <new> -- <component paths>`. The fingerprints are
     equal exactly when that output is empty. The same invocation with `--raw`
     (or `--name-status`) classifies each path from the presence of the path in
     each snapshot and its modes (status letters A, D, M, T). Where path names
     must be carried exactly (Git otherwise quotes unusual names), the
     invocation adds `-z`; that is the one addition to the documented
     command.
   - *Change classes* (identity is the path):
     - **add** (status letter A): the path is in `<new>` and not in
       `<current>`;
     - **remove** (status letter D): the path is in `<current>` and not in
       `<new>`;
     - **rename**: not a class. A move is a remove plus an add, because
       `--no-renames` is part of the command and the materializer installs by
       path;
     - **mode change** (status letter M with different modes, or T): the path
       is in both with a different mode, whatever the object id (an
       executable-bit change is a change; a change of entry type, status
       letter T, is a mode change);
     - **content change** (status letter M with the same mode): the path is in
       both with the same mode and a different object id.
   - *Required tests* (to be written with the code, not by this record):
     identical snapshots give equal fingerprints; each of add, remove, rename
     (reported as remove plus add), mode-only change, content-only change and
     a type change (file to symlink) is detected and classified; a commit that
     changes only paths outside a component gives an equal fingerprint for that
     component, however many commits lie between the snapshots; the order in
     which Git lists entries or the directory layout cannot change the list;
     paths with spaces, quotes, newlines and non-ASCII bytes are carried
     exactly; the empty component is the empty list and an unreadable or absent
     commit is `unknown`, never equal; the result is the same on SHA-1 and
     SHA-256 repositories; neither reading nor comparing writes to either
     repository or touches the network.
   - *No engine code now.* This record specifies the fingerprint and fixes the
     documented command as the way to compute it. It requires no new engine
     code, and none is built now (Decision 8).

3. **Freshness (D3), a specification.**
   - The report compares, for each component, the fingerprint at the pinned
     commit with the fingerprint at the tip of the examined ref of the local
     source. Per component the status is `current` (equal), `changed`
     (different) or `unknown` (the ref, the pinned commit or a tree cannot be
     read). `changed` asserts a difference only; it states neither a
     direction nor a count of commits.
   - The report names the examined ref (its full name) and the time that ref
     was last refreshed in the source, or states `UNKNOWN` for either. The time
     of the last refresh is the time of the newest reflog entry of that ref in
     the source repository; without a reflog it is `UNKNOWN`. It is never
     inferred from the current clock.
   - Freshness is advisory and never changes an exit code (as in ADR-0015).
   - **Refresh is an explicit command.** Updating the examined ref is a
     separately invoked step, and it is the only step of this model that may
     use the network for freshness. No hook, no `verify`, no `status` and no
     checker run performs it. Integrity checks (local integrity and upstream
     provenance of ADR-0015) stay local and offline.
   - No engine code is built for this now (Decision 2, last item). The result
     schema that carries the property changes only when the code is built.

4. **Hooks make no network call (D4).** A hook runs offline: it reads the
   working tree, the index and local objects. The `git ls-remote` drift
   warning in the consumers' pre-push hooks is to be removed. This record
   changes no consumer hook: each consumer decides and executes the removal in
   its own ADR, with the Owner's permission.

5. **Delivery (D5).** The materializer has two targets. The **consumer
   target** is the default and has the roots `scripts/`, `schemas/` and
   `docs/tooltempest/`. The **client target** is selected only by an explicit
   argument and has the roots `skills/` and `rules/`. A target installs only
   the manifest entries of its own roots and reports the entries of the other
   target as out of scope; `docs/reference/` stays reference-only and is
   installed by neither.
   - *Core* is delivered by the existing materializer.
   - *Contract* is a managed file delivered by the same materializer (the same
     plan, refusal, verification and receipt) into the consumer's
     `docs/tooltempest/`. It is a tracked file, and the consumer's
     `.gitignore` does not match it, so that a link to it from a Constitution
     resolves in any clone. A consumer that ignores that path has not adopted
     the contract.
   - *Client* is delivered by the same materializer with the explicit,
     separately invoked client target:
     - the target is never run implicitly: no consumer run, no hook and no
       default selects it, and no run writes outside the root it was given;
     - there is no default client directory: the root is supplied by the
       operator, and the repository contains no default client path and no
       client-specific directory;
     - there is one lock and one receipt per machine, in the client root next
       to the installed files, mirroring the consumer layout:
       `.tooltempest.lock` (the pin, written by the operator, never by the
       tool) and `.tooltempest.receipt.json` (written by apply);
     - it uses the same plan, refusal of unmanaged and edited files,
       verification and receipt machinery, and installs only manifest entries
       of the client component; a file the tool does not manage in the same
       directory is never read as a conflict and never touched.
   - *Retirement of the other installer.* Both consumer copies of
     `scripts/sync-tooling.sh` and the tests that exercise them are removed in
     the consumers, as part of this same decision, and a consumer never runs
     both installers. The removal is made in each consumer, under that
     consumer's own ADR and with the Owner's permission (the rule of
     Decision 4).

6. **Removed paths (D6).** A file that left the manifest stays on disk and
   raises no finding (Context 5, verified from the code and the existing
   test). No path of an installed root (`scripts/`, `schemas/`, the contract
   root, `skills/`, `rules/`) is removed from the manifest until a separate
   accepted ADR defines how removed paths are handled. The first such removal
   is expected at legacy retirement and is a revisit trigger. This record
   defines no orphan record, no orphan receipt field and no deletion flag.

7. **Repin criteria, per component (D7).** An explicit repin made as a step of
   a task is judged by which components changed between `<current>` and
   `<new>`:
   - **core:** the evidence defined in item 7 of the documentation rules
     applies (same checker verdicts on the consumer's committed tree,
     differing payload paths within the manifest, materialize dry run with no
     conflict, `tooling.py verify` exit 0);
   - **contract or client:** the repin needs the Owner's approval of the diff
     of the changed component.
   No tool repins: a repin is always an explicit step.

8. **Out of scope (D8):** rollout (APPLY) across repositories, CI bots, Claude
   Code plugins, tags, an engine cache, a SPEC generator, and engine code for
   the fingerprint and for freshness.

9. **Outside the system (D9).** Claude.ai project instructions and assistant
   memory are to hold only references to these documents, never the text of a
   rule.

10. **Receipt, journal and install lock (D10).**
    - *A new receipt schema version.* The receipt gets a new `schema_version`
      (2). Version 2 adds the contract and client roots (`docs/tooltempest/`,
      `skills/`, `rules/`) to the paths a receipt may hold and adds nothing
      else; version 1 keeps its rules exactly (paths under `scripts/` and
      `schemas/` only).
    - *Reader and writer.* The reader accepts versions 1 and 2, each under its
      own rules; any other value is a malformed receipt. The writer emits the
      lowest version that can express the content: 2 only when the receipt
      holds a path under one of the three roots version 1 cannot hold,
      otherwise 1. A consumer that needs no new feature therefore keeps a
      receipt that earlier tool versions can read, and a receipt written by the
      earlier version is written back unchanged when nothing in it changes.
    - *The journal.* The `installing` journal is a receipt in the `installing`
      state and follows the same version rule and the same field rules
      (including `previous_*`). Recovery reads both versions. An older tool
      refuses a newer journal as an unsupported `schema_version` instead of
      misreading it.
    - *The schema file* `schemas/tooling-receipt.schema.json` is updated to
      describe both versions, closed in each.
    - *Compatibility and version.* The lock format does not change and the
      fingerprint stores no new field. The tool version is bumped when this is
      built; `tool_version` in the receipt records the writer.
    - *Install lock outside Git.* For a root that is not a Git checkout, the
      exclusive install lock is a file inside the root,
      `.tooltempest-install.lock` (the name is chosen here). The tool creates
      the file if it is absent, opens it with `O_NOFOLLOW`, verifies that it is
      a regular file and takes an exclusive `flock` on it. A symlink at that
      name, or any entry that is not a regular file, refuses the run with an
      error before anything is written, and the target of a link is neither
      created, modified nor locked. A second run on an existing lock file
      succeeds when no other process holds the `flock`; a run that finds it
      held is refused as busy.
    - *The in-repository lock is not changed here; follow-up.* A root that is
      a Git checkout keeps its lock in the Git directory, as now (Context 4:
      no `O_EXCL`, no `O_NOFOLLOW`). Applying the same open to it is a
      follow-up, because this record changes the install only for the roots it
      adds, and changing the open flags of the lock that consumers run today
      alters existing behaviour and needs its own test of an existing path.

11. **Precedence over ADR-0015 and revisit triggers (D11).** Wherever a
    statement in ADR-0015 cannot be satisfied together with Decisions 1 to 10,
    this record prevails. ADR-0015 is not edited. The revisit triggers are
    recorded in Confirmation & Revisit.
    - *Textual conflicts with ADR-0015*, quoted from it, as non-exhaustive
      examples:
      - Context & Constraints: "nothing under `~/.claude/skills/` is
        installed, written or modified" (the explicit client target);
      - Decision 2: "`skills/` and `rules/` entries are reported as out of
        scope and never touched" and "any other root makes the manifest
        malformed" (the roots added by Decisions 5 and 10);
      - Decision 4: "the pin against the tip of a local upstream ref" as the
        definition of freshness (Decision 3);
      - Decision 6, in paraphrase and not as a quotation: the receipt it
        defines records the files that the roots of its Decision 2 allow, with
        no other form (Decision 10);
      - Decision 7: "an exclusive non-blocking `flock` on
        `<git dir>/tooltempest-install.lock`", and the Alternatives row "A lock
        file inside the work tree" (a root outside Git, Decision 10);
      - Decision 8: "Only `materialize --apply --allow-network`" as the only
        use of the network (the explicit refresh command of Decision 3);
      - Consequences: "Nothing here touches `~/.claude`" and "skills and rules
        delivery is unchanged and still manual" (the explicit client target).
    - *Implementation facts, not ADR-0015 text.* The current parser and the
      current receipt schema reject what Decision 10 adds: `parse_receipt`
      accepts only `schema_version` 1 and only paths under `scripts/` and
      `schemas/`, and `schemas/tooling-receipt.schema.json` is closed to the
      same (Context 3 and 12). These change when Decision 10 is built; they
      are not conflicts with an ADR-0015 passage.

## Alternatives & Rationale

| Question | Option | Verdict | Why |
|---|---|---|---|
| Components | One bundle (today) | Rejected | A client-only or contract-only change cannot be told from a core change, so the repin evidence of item 7 judges them all alike. |
| Components | Three components | Chosen (D1) | Each has a different consumer, a different failure cost and a different approver (Decision 7). |
| Change signal | Commit distance (ADR-0015) | Rejected | A commit that touches nothing distributed makes a pin look behind; a distance carries no statement about what changed. |
| Change signal | A hash value stored in the lock or receipt | Rejected | Adds a stored field to a format, which the Owner excludes, and stored values can go stale against the tree. |
| Change signal | Subtree object id per root | Not chosen | It signals a change but not which path; the contract is one file, not a directory. The list is the same information and names the paths. |
| Change signal | Canonical list from Git trees, on demand | Chosen (D2) | Uses fields Git already provides, needs no stored state and reuses the documented command. |
| Freshness probe | `git ls-remote` in a hook | Rejected | Network in a hook; a string inequality of SHAs cannot say whether anything distributed changed; it is best-effort and says nothing offline. |
| Freshness probe | Explicit refresh, then local comparison | Chosen (D3) | The network step is separate and visible; integrity stays offline. |
| Contract delivery | Consulted in place or copied by hand into Constitutions (today) | Rejected | Copies drift and nothing compares them (Context 8). |
| Contract delivery | Managed, tracked, non-ignored file | Chosen (D5) | Same machinery; the file is in every clone, and its diff is visible on a repin (which is what Decision 7 asks the Owner to approve). |
| Contract location | Inside `scripts/` or `schemas/` | Not chosen | Needs no format change, but a consumer-facing document would sit in a code or schema directory, and a change to it could not be told from a core change by root. |
| Contract location | A dedicated root `docs/tooltempest/` | Chosen (D1) | The contract component needs its own component paths, so that the fingerprint and the repin rule apply to it alone. `docs/reference/` is not reused: Decision 1 keeps it outside every component, and the tool treats it as reference-only. ADR-0008 does not forbid delivery into a consumer repository (it excludes a client configuration directory as a destination and allows a consumer to vendor its own copy); it is not the reason. |
| Client delivery | **A**: separate Client Adapter under `clients/claude-code/` with its own lock and receipt | Rejected | Needs a second installer or a copy of the tested plan, refusal, receipt, recovery and descriptor-relative write code together with its tests; puts a client-specific directory in a repository whose README commits to having none; two receipt formats to keep apart. |
| Client delivery | **B**: the same materializer, explicit client target | Chosen (D5) | Reuses the tested mechanism; the changes are local: target-specific roots, the receipt path check and schema, the install lock of a non-repository root, a target argument, and the tests that pin the old limits. |
| Receipt format | Widen receipt version 1 in place | Not chosen | The parser and the schema are closed to version 1 only (Context 12): a consumer that needs nothing new would still get a receipt an earlier tool cannot read. |
| Receipt format | A new version 2 written only when needed | Chosen (D10) | The reader accepts both versions; the writer emits the lowest version that expresses the content; the lock does not change; the fingerprint stores nothing. |
| Lock outside Git | Fail with `NOT_A_REPOSITORY` (today) | Rejected | A client root is not expected to be a repository (Context 4). |
| Lock outside Git | A lock file inside the root, created if absent, opened without following links | Chosen (D10) | The root is operator-supplied, so a link at the lock name must not redirect the lock or the install. |
| Removed paths | Define orphan handling now | Not chosen | The Owner decided that no path is removed from the manifest until a separate accepted ADR defines how removed paths are handled; no path of an installed root has been removed yet (Context 5, fact 2). |

**Conflicts to settle when the parts are built (not resolved by this
record).** (a) The client target contradicts README sentences on client
independence (Context 11): the adapter being "the job of a separate client
adapter" and ToolTempest "delivers nothing to a client directory". It does
not contradict the commitment to contain no client-specific directory,
provided Decision 5's conditions hold. (b) It contradicts "never touches
`~/.claude`" and "outside the consumer" statements that are source text and
tests: the docstrings of `scripts/tooling.py`, of
`scripts/tooling_core/__init__.py` and of `scripts/tooling_core/engine.py`;
the comment on `GLOBAL_ROOTS`; the README; and the tests `test_materialize.py`
(global files untouched), `test_scenarios.py`, `test_safety.py` and
`tooling_helpers.py` (their docstrings). The behavioural tests stay true for
every run without the explicit target, which is the property Decision 5
requires; the list is not claimed to be exhaustive. (c) Not a conflict with
ADR-0008, stated to prevent a wrong reading: ADR-0008 makes `docs/reference/`
a category of documents consulted in place, not installed as client rules,
with no `~/.claude` destination, and lets a consumer vendor its own copy. It
does not forbid delivering a managed file into a consumer repository.

## Consequences

- No engine code is built for the fingerprint or for freshness. Until it is,
  ADR-0015's freshness (commit distance) is the implemented behaviour
  (Context 6), and the fingerprint comparison can be done by hand with the
  documented command.
- The delivery and the receipt format (Decisions 5 and 10) change behaviour
  when they are built: a manifest with the contract root and the client roots,
  the explicit client target, receipt version 2 and the lock outside Git. The
  README and the source docstrings that become false are corrected in the
  changes that build them (Conflicts).
- The consumer contract is extracted from item 7 without a change of meaning.
  Item 7 of the documentation rules treats a change of any payload path alike
  (Context 9), while Decision 7 asks for the Owner's approval of a contract
  or client diff; making the contract text say so changes the meaning of the
  obligation and is not done by building this record.
- `scripts/check_manifest.py` compares the manifest with the tracked files of
  its `COMPOSITION_DIRS` (observation: `grep -n COMPOSITION_DIRS
  scripts/check_manifest.py` at the commit of Context); the dedicated root is
  added to it with the contract file, or a manifest line under it would go
  unchecked.
- Rollback hazard, limited by the writer rule of Decision 10: a consumer whose
  receipt stays at version 1 is unaffected. Where a version 2 receipt or
  journal was written, a consumer that pins an older version again runs an
  older tool that rejects it as `RECEIPT_MALFORMED` (Context 12); the receipt is
  then moved
  aside by hand (the tool never overwrites a malformed receipt) and rebuilt by
  `materialize --apply`.
- A consumer whose `docs/tooltempest/` already holds an unmanaged file with
  other content gets the existing refusal `CONFLICT_UNMANAGED`, not an
  overwrite (observation: `grep -n CONFLICT_UNMANAGED
  scripts/tooling_core/engine.py`).
- A committed contract file makes a contract change appear in the consumer's
  diff on repin. With the receipt not tracked, a fresh clone holds the file
  without a receipt, and the materializer's existing `record` action
  (identical bytes, receipt only) applies (observation: `grep -n '"record"'
  scripts/tooling_core/engine.py`).
- ADR-0015 is left unedited. Observation: this repository has no ADR index
  (`git ls-files | grep -i -E 'adr-index|generate_adr_index'` prints nothing
  at the commit of Context); the documentation rules say to build one only
  when the scale justifies it, so nothing is regenerated.
- A file that leaves the manifest stays on disk unreported (Context 5) until
  a separate ADR defines removed paths; the first removal of a path of an
  installed root is expected at legacy retirement.

## Confirmation & Revisit

**Evidence at drafting** (2026-11-04, `6510b75`, read-only). The sources of
Context 1 to 12 were read at the locations given there. The behaviour of
`git diff --no-renames` for add, remove, rename and mode change was observed on
a scratch repository with Git 2.44.0 (Context 9); the newest reflog entry of
`refs/remotes/origin/main` in this checkout carries a timestamp
(`git reflog show --date=iso refs/remotes/origin/main -n 1`).

**Acceptance criteria**, each a property to be verified once the part is
built; the part is not accepted as implemented until its criteria pass:

1. *Fingerprint.* The tests listed in Decision 2 pass, and for any two
   commits the component's differing paths equal the output of `git diff
   --no-renames --name-only -z <current> <new> -- <component paths>`, for
   unusual path names too.
2. *No stored state.* The fingerprint adds no field to the lock or the
   receipt: computing and comparing it writes nothing.
3. *Freshness.* For fixture repositories: equal component trees at different
   commits report `current`; a changed path reports `changed` for exactly its
   component; a missing ref reports `unknown`; the report names the ref and
   its last-refresh time or `UNKNOWN`; the exit code is unaffected.
4. *Offline.* `verify`, `status`, the checker and a materialize dry run make no
   network call (the existing network tests still pass); only the explicit
   refresh command may.
5. *Hooks.* No hook of a consumer contains `ls-remote`, `fetch`, `curl` or
   `wget` after that consumer's own ADR; checked in that consumer by its own
   test or by `grep`.
6. *Contract.* In a disposable consumer whose `.gitignore` is the usual one
   for vendored files, `git check-ignore` does not match the contract file
   after `materialize --apply`, its bytes and mode equal the pinned blob, the
   tool does not create or change a `.gitignore`, and `git ls-files` lists the
   file once the consumer commits it.
7. *Client target.* Invoking the client target without a root argument fails
   with a usage error and writes nothing; with `HOME` set to a throwaway
   directory, no run of the consumer target creates or modifies anything
   under it; `git ls-files` lists no path under a client-specific directory
   (`.claude/`, `.cursor/`, `clients/`); with an explicit root, only manifest
   entries of the client component are written; an unmanaged or edited file is
   refused with bytes and mode preserved; a second apply changes nothing;
   `verify` of the target reports local integrity and provenance; the lock and
   the receipt are read from and written to the client root.
8. *Manifest, lock and tool version.* A manifest with `docs/tooltempest/`,
   `skills/` and `rules/` lines maps each to its target, and an unknown root is
   still `MANIFEST_MALFORMED`; the lock parser accepts and rejects exactly
   what it did; the tool version is bumped.
9. *Receipt version 2.* A version 2 receipt with a path under
   `docs/tooltempest/`, `skills/` or `rules/` is valid; the same content with
   `schema_version` 1 is rejected.
10. *Reader.* Version 1 receipts, including a fixture taken from the tool
    version before the change, parse and verify under the new tool; a
    `schema_version` other than 1 or 2 is `RECEIPT_MALFORMED`.
11. *Writer.* A run whose receipt holds only paths under `scripts/` and
    `schemas/` writes version 1, byte-identical to what the earlier tool
    version wrote for the same input apart from `tool_version`, and writes
    nothing when nothing changes (an existing version 1 receipt is not
    rewritten); a run with a path under a new root writes version 2.
12. *Journal.* A version 2 journal is written before the first replacement,
    is read by recovery, and an interrupted installation is finished by a
    repeated apply; a version 1 journal is still read; an older reader (the
    version gate of the earlier tool) refuses a version 2 journal and a
    version 2 receipt instead of misreading them; `previous_*` fields without
    the `installing` state are rejected.
13. *Schema.* The schema file validates the version 1 and version 2 fixtures
    of criteria 9 to 12, rejects version 1 with a new root, rejects a receipt
    with an unknown field in either version, and a test shows that the field
    set the parser accepts equals the schema's.
14. *Root outside Git.* `materialize --apply` on a root that is not a Git
    checkout creates `.tooltempest-install.lock` inside the root when it is
    absent, opens it without following links, verifies that it is a regular
    file and takes an exclusive `flock`; a second run on the existing lock file
    succeeds when no other process holds the `flock`; a run that finds the
    `flock` held is refused as busy; a symlink at the lock name is refused with
    an error, nothing is written and the link's target is not created,
    modified or locked; a directory or another non-regular entry at that name
    is refused; a root that is a Git checkout still uses its Git directory.
15. *Test isolation.* No test touches the real `~/.claude`: a guard fails the
    test run if one does, and a test shows that the guard fires.
16. *Removed paths.* The test of Context 5 still passes: a path that left the
    manifest stays on disk, and no orphan record, orphan field or deletion
    flag exists.
17. *Repin.* A fixture where only a core path changed passes the documented
    evidence; a fixture where a contract or client path changed is reported as
    needing the Owner's approval of the diff.
18. *Disjoint components.* No path of a manifest is in two components.

**Revisit triggers.** Each is checkable:

- **(a) A fourth consumer repository**, counted by origin URL. A consumer is a
  repository that tracks a `.tooltempest.lock` (`git ls-files
  .tooltempest.lock` prints it); checkouts whose `git remote get-url origin`
  is the same URL, with any trailing `.git` removed, are one repository, so
  worktrees and extra clones are not counted again. Two repositories are known
  from the Owner-held consumer audit of 2026-11-03, an audit that this
  repository does not contain.
- **(b) A second incident within a quarter** of a false version signal or a
  repin for an unchanged payload.
- **(c) A second AI client**: a client other than Claude Code that receives
  ToolTempest-delivered files. Claude.ai project instructions and assistant
  memory (Decision 9) receive no ToolTempest file and do not count.
- **(d) The first removal of a path of an installed root** (`scripts/`,
  `schemas/`, the contract root `docs/tooltempest/`, `skills/`, `rules/`) from
  `MANIFEST.txt`, expected at legacy retirement (Decision 6): a commit whose
  diff of `MANIFEST.txt` removes a line starting with one of those roots
  (`git log -p --format= -- MANIFEST.txt | grep -E
  '^-(scripts|schemas|docs/tooltempest|skills|rules)/'` prints it; at the
  commit of Context it prints nothing). The earlier removal of a
  reference-only entry (Context 5, fact 1) is not such a removal.

**Where the triggers are recorded.** Here only, in this section. Observation:
ToolTempest keeps no item registry (`git ls-files | grep -i -E
'backlog|registry|tracker'` prints nothing, and `git log --all --name-only |
grep -i backlog` prints nothing, at the commit of Context). This record reads
the line of the maintainer file CLAUDE.md that says not to fabricate a
five-document canon as meaning that no BACKLOG document is added to this
repository; that reading is the record's, not a statement of CLAUDE.md.

## Source

The Owner's decisions D1 to D11 of 2026-11-04 and the Owner's resolutions of
the same date on their wording, the Owner-held consumer audit of 2026-11-03
(not contained in this repository), ADR-0006, ADR-0008, ADR-0013 to
ADR-0016, the README and `docs/reference/documentation-rules.md` at
`6510b75`. No external AI cross-check was made.
