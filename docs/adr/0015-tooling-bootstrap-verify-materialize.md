# ADR-0015: Tooling Bootstrap — Verify, Status and Materialize From Verified Git Objects

## Status

Accepted, 2026-10-23. Implements Stage 3 of the DocOps tooling redesign
(reproducible tooling installation). Accepted by the Owner together with
one correction made during acceptance: the tool never infers that a file is
its own from the file's name (Decisions 5 and 7). It supersedes nothing and changes no
consumer: the consumers' `scripts/sync-tooling.sh` scripts keep working
unchanged, and adopting `scripts/tooling.py` in a consumer is a separate
decision.

## Context & Constraints

**Governing sources, stated explicitly.** The approved requirement source
is the Stage 0 plan (`pasted-content-id-95eb-evidence-and-enchanted-sprout.md`).
It contains two Stage 3 items: finding F10 (a clean worktree of Brain has
no `scripts/doc_sync.py` or `scripts/doc_sync_tier2.py`, so the pre-commit
hook dies in a raw Python error) and acceptance criterion B7 (in a clean
Brain worktree the missing modules are identified before the hook is
activated). The plan has no section describing the interface, and it fixes
no command names, receipt format or exit codes (checked by reading it).
The interface and every rule below come from the Owner's Stage 3 instruction
of 2026-10-23, which named `scripts/tooling.py` with `verify`, `status` and
`materialize` as "the established tooling command surface". No such file
existed in this repository before this stage (checked: file listing and
`git grep`); this record creates it. The unapproved draft contract and the
Codex report were not used as requirement sources.

Prior art, read and not changed: a consumer's `scripts/sync-tooling.sh`
(the article-pipeline and the clean Brain copies differ in how much of the
manifest and the destinations they validate, and share everything listed
here; Radar has none). It clones or updates
`~/.cache/tooltempest/repo`, runs `git fetch` as its first step, checks the
pinned commit out into that cache and copies files from the checkout. It
refuses to overwrite a divergent file, but it keeps no record of what it
installed, reads bytes from a mutable working tree, always touches the
network when a cache exists, and installs `skills/` and `rules/` into
`~/.claude`. `.tooltempest.lock` has four fields: `repository`, `commit`,
`path`, `sha256` (the last two name one file, `scripts/doc_sync.py`).
`MANIFEST.txt` at the pinned commit lists the tracked files (ADR-0006).

Constraints: standard library only; read-only `verify` and `status`;
no fetch, repin, install or overwrite during verification; network only when
explicitly authorised; no automatic repin; no change to article-pipeline,
Brain or Radar; nothing under `~/.claude/skills/` is installed, written or
modified, and global skill activation and conflicts between global skill
sets are out of scope.

## Decision

1. **A separate command surface.** `scripts/tooling.py` and the package
   `scripts/tooling_core/` (`objects.py`, `declarations.py`, `engine.py`,
   `fsops.py`, `cli.py`). It shares no code with `scripts/docops.py`: the checker judges
   a snapshot's documents, the tooling reproduces dependencies.

   ```
   tooling.py verify      [--root DIR] [--lock FILE] [--receipt FILE] [--source DIR]
                          [--upstream-ref REF] [--format text|json]
   tooling.py status      (same options)
   tooling.py materialize (same options) [--apply] [--allow-network]
   ```

   `--root` is the consumer checkout (default: the Git top level of the
   current directory); a linked worktree works. `--source` is a ToolTempest
   repository holding the pinned commit (default `~/.cache/tooltempest/repo`,
   the cache `sync-tooling.sh` already uses); only its Git objects are read.
   `verify` and `status` do not accept `--allow-network`.

2. **Inputs.** The lock keeps its four fields and gains one optional field,
   `files: [{path, sha256}]`, to declare hashes for more than one file; the
   legacy `path`/`sha256` pair is a declaration for that one file. Unknown
   fields, duplicate JSON keys, a branch name or abbreviation instead of a
   full object id, malformed or colliding paths are rejected. The file list is
   `MANIFEST.txt` at the pinned commit. Entries under `scripts/` and
   `schemas/` are installed at the same relative path; `skills/` and
   `rules/` entries are reported as out of scope and never touched;
   `docs/reference/` entries are reported as consulted in place (ADR-0008);
   any other root makes the manifest malformed, as in `sync-tooling.sh`. A
   consequence: a future manifest root needs a tool update before `verify`
   passes again.

3. **Bytes come from verified Git objects only.** The pinned commit is
   resolved by walking the object graph: the commit, then each tree on the
   path, then the blob, and every object read is re-hashed (its object id must
   equal the hash of `<type> <size>\0<content>`, SHA-1 or SHA-256 by the
   pin's length). A mutable checkout, a dirty or moved cache, a tampered
   object stored under a wrong name, or a different remote cannot change what
   is installed. The source's `remote.origin.url` is reported
   (`remote_matches_lock`) but is not a trust decision: the commit id is the
   identity. Only regular files (modes 100644 and 100755) are installable; a
   symlink, submodule or directory source entry is refused. A caller's `GIT_*`
   environment is removed from every Git call, so a hook's `GIT_DIR` cannot
   redirect the tool.

4. **Three properties, never merged** (`summary.local_integrity`,
   `summary.upstream_provenance`, `summary.freshness`):
   - *local integrity* — installed bytes and the executable bit against what is
     declared for them. The hash comes from the lock if it declares one, else the install
     receipt (for the pinned commit, or for an earlier one when the file has
     the same bytes and mode at the pin, so a moved pin does not make an
     unchanged file look stale). The lock declares no mode, so the executable bit
     is the receipt's, or the verified pin's when no receipt applies. Per file: `ok`, `missing`,
     `modified`, `outdated` (a known-managed earlier version), `stale` (an
     interrupted installation), `unrecorded` (no declaration at all),
     `unsafe`. Property: `ok`, `failed`, or `unknown` (nothing to judge by).
   - *upstream provenance* — that declared hash against the sha256 of the
     verified blob at the pin: `verified`, `mismatch`, `undeclared`,
     `unknown` (the source is unavailable). Property: `ok`, `failed`,
     `unknown`.
   - *freshness* — the pin against the tip of a local upstream ref
     (`--upstream-ref`, else `refs/remotes/origin/main`, else
     `refs/heads/main`): `current`, `behind` (with a count), `not_on_ref`, or
     `unknown`. Nothing is fetched to find out; the answer is only as fresh as
     that ref. It never changes the exit code.

   `verify` exits 0 only when local integrity and provenance are both `ok` and
   no error finding exists. A property that cannot be established is not a
   pass (`unrecorded` and `undeclared` files exit 1 until `materialize
   --apply` records them). `status` reports the same document and exits 0
   unless the tool itself failed.

5. **Materialize plans first and is all-or-nothing.** Dry run is the default;
   `--apply` is the opt-in. Per file the plan is one of `create`, `upgrade`
   (the file's bytes are exactly the hash the receipt recorded for an earlier
   version, same executable bit), `record` (bytes already equal the pin, only
   the receipt changes), `current`, or `refuse`. A refusal is one of
   `CONFLICT_MANAGED_MODIFIED` (the receipt lists the file but its bytes or
   mode differ: a local edit), `CONFLICT_UNMANAGED` (no receipt entry, bytes or
   mode differ from the pin), or `PATH_UNSAFE`. Any refusal, or any structural
   error (a bad lock, manifest, object, a declared hash that is not the blob),
   writes nothing at all. No file that existed before the run is deleted (an upgrade replaces the earlier
   version in place); a file that left the
   manifest stays on disk and leaves the receipt. Nothing is ever repinned.
   The only things this tool removes are the temporary copies and the empty
   directories the current run created (a staged file beside a destination,
   the receipt's temporary file), and only on that run's own failure paths.
   Ownership comes from the run having created the object itself (an
   exclusive create, or a `mkdir` that succeeded) and having recorded its
   device and inode, never from a name: a file that merely looks like a
   temporary file is never removed, overwritten or reused, and a removal is
   never made through a path string (see 7). A `materialize` result lists its plan and its refusals, not the
   findings (missing, outdated, unrecorded) that the plan resolves; `verify`
   lists those.
   Bytes identical to the pin but unrecorded (an older installer's work) are
   adopted without being rewritten.

6. **The install receipt** (`.tooltempest.receipt.json` beside the lock;
   `schemas/tooling-receipt.schema.json`) records `repository`, the source
   `commit`, and per file `path`, `sha256`, `git_blob` (checked against the
   pinned blob whenever the receipt vouches for a file) and `mode`, with a
   `state` of `complete` or `installing`. It is written only by `materialize
   --apply`, atomically, and a malformed receipt is reported and never
   overwritten. Whether to commit it or ignore it is the consumer's choice.

7. **Installation protocol.** A plan with nothing to write, and any refused
   or dry run, takes no lock and changes nothing. A plan with writes takes an
   exclusive non-blocking `flock` on `<git dir>/tooltempest-install.lock` (the
   per-worktree git directory, so a linked worktree and its main checkout do
   not block each other) and re-reads everything under it; a second concurrent
   run is rejected as `INSTALL_LOCKED`, and the lock dies with its process.
   Under the lock: stage every new file beside its destination, each as a new
   exclusive create (`.tooltempest-stage.<pid>.<n>`, or the same name with a
   numeric suffix when that name is taken; an existing path is never opened
   for writing, and after 64 taken names the run fails with `STAGING_FAILED`
   having changed nothing); mode is set explicitly so the umask does not
   decide it. Every create, rename and removal of the installation is made
   relative to directory descriptors (`fsops.py`): the checkout root is opened
   once, each directory below it is opened relative to the descriptor of its
   parent with `O_NOFOLLOW|O_DIRECTORY`, the missing ones are made with
   `mkdir` relative to the parent, and the descriptors are held until the
   installation ends. A symlink put in place of any component afterwards cannot
   redirect an operation, because the held descriptor still names the directory
   that was opened. Before each rename the path that leads to the directory is
   walked again, component by component without following links, and must
   still lead to the held directory, otherwise the run stops as interrupted. A
   staged copy is removed (and a staged copy is renamed) only while its name,
   looked up in the held directory, still refers to the inode that was
   created; a directory the run made is removed with `rmdir` only while its
   name in the held parent still refers to it, and only if empty. The run
   keeps an open descriptor on every file and directory it made until it ends,
   so the inode cannot be freed and handed to another file, and a matching
   device and inode number can only be the object that was made. If the
   platform lacks the descriptor-relative primitives (`UNSUPPORTED_PLATFORM`),
   the run fails before writing anything; if a directory cannot be opened
   without following a link, it stops (before writing, or as interrupted
   with the cleanup above). There is no path-based fallback. Leftovers of an interrupted run are not removed: the journal
   does not record staging names, so nothing establishes that a file with such
   a name belongs to this tool. They are kept and `materialize` reports each
   one as a `STAGING_FILE_PRESERVED` warning with the advice to check it and
   remove it by hand; the installation does not depend on them. Then write the receipt as a journal (`state: installing`, with
   each file's `previous_sha256` and `previous_mode`, so an edit of the
   executable bit during an unfinished installation is refused like any
   other edit); rename the staged files into place,
   re-inspecting each destination first; write the `complete` receipt; then
   re-verify and report `post_verification`. A failure while staging undoes
   everything (staged files and directories it created) and the destinations
   and the receipt are untouched. A failure or a kill after the journal, or a
   failure writing the final receipt after every file was replaced, leaves
   the journal: `verify` and `status` report `RECOVERY_REQUIRED`, and a repeated
   `materialize --apply` finishes the work, recognising each file as either
   its pre-install or its new version, and still refusing anything edited in
   between. Recovery is forward only; there is no rollback.

8. **Network.** Only `materialize --apply --allow-network`, only when the
   pinned commit object is absent from the source, only for that one commit
   (`git fetch <repository> +<commit>:refs/tooltempest/pins/<commit>`; no
   branch, tag or working tree is touched), and only from an `https://`,
   `file://` or absolute-path repository value; `ext::`, `ssh`, `git`,
   `http`, relative paths and values with control characters are refused. A
   missing source directory is created as a bare repository only on this
   path. Without permission a missing object is a clear failure
   (`PIN_OBJECT_MISSING`) that names the flag, and nothing is fetched.

9. **Path safety.** Every path is validated (no absolute path, empty, `.` or
   `..` component, backslash, control character or `.git` component; Unicode
   and case-insensitive collisions and file-inside-file destinations count as
   duplicates). A destination whose ancestor is a symlink or a non-directory,
   or which is itself a symlink or a non-regular file, is `unsafe` and is
   neither read through nor replaced. Receipts, locks and manifests that are
   symlinks are refused.

10. **Exit codes** (also in `summary.exit_code`; `schemas/tooling-result.schema.json`):
    0 ok; 1 findings, a refusal, an unfinished installation or a busy lock;
    2 the tool could not run (usage, missing `git`, I/O, internal error;
    an `error` object is present). Bad inputs (lock, manifest, receipt,
    objects that are missing, corrupt or of the wrong type) are findings,
    exit 1. Git itself failing (the process dies, the stream is cut short,
    the binary is absent) is a tool failure, exit 2.

11. **Packaging.** The new files are listed in `MANIFEST.txt`. A consumer
    receives them only by an explicit repin; until then the tool is run from a
    ToolTempest checkout, and the bytes it installs come from the pinned
    commit's objects, not from the running copy.

## Alternatives & Rationale

| Option | Verdict | Why |
|---|---|---|
| Fix `sync-tooling.sh` in each consumer | Rejected | Three copies drift, JSON is parsed with `grep`/`sed`, the failure paths (interruption, concurrency, tampering) cannot be tested, and B7 needs one mechanism. |
| Add subcommands to `docops.py` | Rejected | The Owner fixed two command surfaces; the checker is read-only by contract (ADR-0013). |
| Read bytes from the cache's checked-out working tree | Rejected | A dirty, moved or wrong-remote checkout would change what is installed; that is the current behaviour this stage removes. |
| `git show <commit>:<path>` per file | Rejected | Trusts Git's answer without re-hashing; a tampered object under a valid name would pass. |
| Declarations only from the receipt | Rejected | A receipt written from the blob can only prove later divergence, not that the pin is the intended one; the lock is the consumer's own declaration. Both are used, the lock first. |
| Backups plus rollback on failure | Rejected | Needs a second copy of every replaced file and its own failure modes; a journal plus a deterministic forward retry reaches a verified state with less machinery. |
| A lock file inside the work tree | Rejected | It would appear in `git status`; the per-worktree git directory is invisible and exactly per checkout. |
| Always fetch first (the current script) | Rejected | Contradicts "offline when the object is local"; a fetch is only needed when the object is absent. |

## Consequences

- A fresh clone or linked worktree can check, before any hook runs, that its
  dependencies exist and are the pinned bytes: `verify` names each missing
  file and the exact command to fix it instead of a Python traceback.
- Consumers keep their own installers until they adopt this one. Nothing here
  touches `~/.claude`, so skills and rules delivery is unchanged and still
  manual; installing them and resolving conflicts between global skill sets
  remains out of scope.
- A lock that declares only `scripts/doc_sync.py` (all existing locks) makes
  the other files' provenance rest on the receipt, which is derived from the
  same blob. That detects later divergence and a damaged cache, but it is not
  an independent statement that the pin is the intended release. A consumer
  that wants more declares more hashes in `files`.
- Deletion is ownership-based, never name-based. The tool removes only a
  temporary file that the same run created exclusively; a pre-existing file
  at a name it would have used (even `.tooltempest-stage.<digits>.<digits>`)
  is never overwritten or deleted, and the run picks another name. The cost:
  staged copies left by a killed run stay on disk until the user removes
  them, and are reported on every later `materialize` that writes beside
  them.
- Cleanup is descriptor-relative: swapping a directory for a symlink (at any
  level, before or after the run's own checks) cannot make the tool create,
  rename or remove anything outside the directory it opened. What cannot be
  closed on POSIX, because `unlink` and `rename` take a name and not an inode:
  between the identity check of a name and the call that uses it, a process
  with write access to that same held directory could put another file at that
  name. The window is two system calls, and only an actor inside that
  directory can use it.
- `apply` needs POSIX file locking (`fcntl`); verification does not.
- Freshness is advisory and local; a stale cache reports `current` for an old
  tip.
- Residual race: a process other than this tool that edits a destination
  between its last inspection and the rename of that one file can still lose
  or win; the window is a single rename and is checked on both sides.
- Fetching by commit id depends on the remote allowing it (GitHub does); a
  server that does not gives `FETCH_FAILED` with Git's message.
- SHA-1 repositories are verified as strongly as SHA-1 object ids are.
- The default cache directory is created only when `--allow-network` is given
  and the cache is absent, and the output says whether the network was used.

## Confirmation & Revisit

The figures below are the author's runs of 2026-10-23 on the final staged
state; the mutation log is not kept in the repository.
`python3 -B -m unittest discover -s tests/tooling -t tests/tooling`:
223 tests, all passing on Python 3.14.5 and 3.9.6 (the DocOps suite,
`tests/docops`, is unchanged: 340 tests, also passing on both). Mutation
testing: one full pass over the final source, 207 deliberate mutations of the
five modules, each caught by at least one test; the pass that preceded it
(206 of 207) left one mutation alive, a descriptor that leaked when writing a
staged file failed, and the test for it was strengthened before the final pass,
which was run in full again. One mutation that cannot be told from the
original is kept out of the count: recording a previous hash for a file that
is being created cannot differ from recording none, because that destination
is absent.
Earlier runs on earlier states, and the independent code reviews, showed
further gaps that were closed before the final pass: a tree entry whose object
is not a blob, a bad stream header that must reset the reader, an oversized
manifest, unsorted receipt keys, destination checks that were masked by
`O_NOFOLLOW` (now asserted by message), a single diagnosis per cause,
properties for outdated, stale and unsafe files, and an empty installable set.
The tests also found, while the tool was written: a journal-write failure that
left directories it had created; a receipt `tool` field that the parser did
not check; and a moved pin that made unchanged files look outdated. Reviews
found: in an unfinished installation a change of a managed file's executable
bit alone was upgraded over instead of refused (the journal now records
`previous_mode`); the tool deleted a user file whose name matched its staging
pattern, and the same for the receipt's temporary file (now created
exclusively, never judged by name); an unlink of a staging name after its copy
had been renamed away; and cleanup that acted through path strings, so a
directory swapped for a symlink during a run could redirect a removal (now
every operation goes through held directory descriptors, Decision 7). Each has
a regression test; for the last, the tests plant a file with the staging name
and a directory with the created one's name in the directory the link points
at, and perform the swap after the run's own checks have passed. The author
observed the earlier regression tests failing against the earlier source; that
source is not kept in the repository, and for the descriptor fix the evidence
is the mutation pass (every mutation that restores a path-based removal is
caught).

What the tests prove, by requirement:

| Requirement | Proven | Not proven |
|---|---|---|
| Fresh clone, linked worktree (`.git` is a file) | dependencies are missing before and acquired after; the worktree has its own lock and receipt; the main checkout is untouched; the diagnosis needs neither network nor source | a real Brain worktree (not touched, by instruction) |
| Three properties apart | each can fail while the others hold; unknown is not a pass; freshness never changes the exit | freshness against a live remote |
| Verified objects | tampered blob, tree and commit; wrong object type; a blob that is not a commit; a dirty, moved, wrong-remote cache; a linked worktree as source | an attacker who can forge a SHA-1 collision |
| Offline and network | cached object with an unreachable remote; missing object without permission fetches nothing; with permission only the pin is fetched, into a new or a stale cache, sha1 and sha256; unsafe repository values refused | a real HTTPS remote |
| Conflicts | known-managed upgrade; edited managed file and unmanaged file each refused with bytes and mode kept and nothing else written; mode-only edits; identical unmanaged adopted without a rewrite | |
| Files that only look like temporary files | a user file at the exact staging name, with known bytes and mode, is not overwritten, deleted or reused by materialize, dry run, cleanup after a staging failure, cleanup after an interrupted rename, a failed receipt write, or recovery after a kill; exhausted names fail with nothing changed | a directory swapped for a symlink at any level, before the staging of a later file, before a rename, or between the journal and the rename: a user file with the staging name and an empty directory with the created one's name in the directory the link points at stay as they were, bytes and mode, in all three cleanup paths and for the receipt's directory; platform without the primitives fails with nothing written | an actor with write access to the very directory the run holds, acting between the identity check of one name and the system call on it |
| Interruption | staging failure; failure after the first rename; process killed between renames and before the journal; edit during the interrupted state; recovery to a different pin | power loss (no `fsync` verification) |
| Concurrency | held lock rejects a writer and changes nothing; six simultaneous writers end in one complete installation; the lock dies with its holder; main checkout and worktree do not block each other | other platforms |
| Modes | the executable bit and 0644 survive umask 077; a mode change upstream is applied | |
| Scope | nothing under HOME is written; `skills/` and `rules/` are never installed; no `~/.cache` is created by verification | |
| Brain's pre-fix condition | a fixture with the lock, the hash of `doc_sync.py` and a hook calling it: the raw hook fails, `verify` exits 1 naming the file and the command, with no traceback | the real repository |

Revisit when a consumer adopts the tool (the receipt location, whether the
default cache is right, whether the lock needs more declarations), when a
manifest gains a new root, or if a remote refuses fetch-by-commit.

## Source

Stage 3 of the DocOps tooling redesign: the Owner's instruction of 2026-10-23,
the Stage 0 plan's F10 and B7, and the prior art read in two consumers'
`scripts/sync-tooling.sh`. Designed and implemented by Claude Code; no
external AI was consulted.
