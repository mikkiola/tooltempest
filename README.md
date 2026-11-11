# ToolTempest

Client-agnostic canonical source for a small set of shared tooling
primitives: `/spec`, `/verify`, `drift-control`, and (as of V2) the
`doc-sync` skill of the DocOps Protocol. This repository holds only the primitives
themselves — no client-specific integration code lives here.

The rules for documents and process are in
[`docs/tooltempest/universal-rules.md`](docs/tooltempest/universal-rules.md).
They are binding here and in every consumer. Start with its section "How every
repository works".

## What this is

ToolTempest exists to give these primitives one canonical home,
independent of any particular AI coding tool or client. It was split
out of the Article Pipeline project, where the same three files were
previously kept as local, client-specific configuration.

## Composition (V2)

Four mechanism files, all markdown:

Markdown primitives (V1, unchanged):
- `skills/spec/SKILL.md`
- `skills/verify/SKILL.md`
- `rules/drift-control.md`

DocOps Protocol (V2, added by ADR-0001 — `docs/adr/0001-docops-protocol.md`):
- `skills/doc-sync/SKILL.md`

The mechanism files are passive instructional text a client interprets on its
own terms, by reading it. The executable code in this repository, the DocOps
checker and the tooling bootstrap below, is source that a consumer pins and
runs itself; it is listed in `MANIFEST.txt` but is not one of the four files.
The V2 hook entry points of ADR-0001 and ADR-0002 (`scripts/doc_sync.py`,
`scripts/doc_sync_tier2.py`) and their audit-record schema were retired and are
no longer shipped; the two ADRs stay as the record of what they were.
Consuming the checker means executing pinned code via git hooks the consumer
wires itself, not only copying instructional text into a client's config
directory.

DocOps checker (added by ADR-0013 — `docs/adr/0013-docops-checker-spec-state-marker.md` —
and extended by ADR-0014 — `docs/adr/0014-docops-impact-report-validator.md`).
It is source only: tracked and listed in `MANIFEST.txt`, but not part of the
four-file Composition (V2) above. A consumer adopts it by pinning a commit and
wiring its own hooks; a consumer whose hooks still call the retired
`doc_sync.py` has not migrated, and runs a copy installed from an earlier pin.
A change here changes no consumer until that consumer repins.
- `scripts/docops.py` — read-only `check` / `report` of an exact Git snapshot
  (`--tree index|<commit>`, `--push-stdin`), optionally validating an
  impact report that a reviewer wrote (`--impact-file PATH|-`); never
  writes, stages, commits, pushes or uses the network
- `scripts/docops_core/` — the standard-library package behind it
- `schemas/docops-result.schema.json`, `schemas/docops-policy.schema.json`,
  `schemas/docops-impact-report.schema.json`

It evaluates SPEC structure. Given an impact report it also checks that the
report is well-formed, bound to the snapshot under check, complete against
the change and current, and never whether its statements are true. Every
other result dimension is reported as `assessed: false` (it carries no
status). A SPEC declares "no active task" in its frontmatter (`---` /
`spec-state: idle` / `---` at the top of the file). The root `SPEC.md` is
always evaluated, exactly once, with or without a policy file. An optional
`.docops-policy.json` adds registered active tracks (`specs`) and lists
non-active files (`non_active_specs`: exact path and reason, such as a test
fixture); an explicit root entry stays valid. Every tracked file named
`SPEC.md` (any depth, case-insensitive) must be the root SPEC, a registered
active track or a listed non-active file; any other is a FAIL (exit 1) that
names the path, as are a path in two categories, a duplicate or stale
non-active entry, a registered path that is a directory, and a track named
`root` at another path beside a root `SPEC.md` (only when the policy has no
explicit root entry). Other spellings such as
`SPEC.txt` are not detected. An absent root `SPEC.md` that no policy entry
names is not a failure of this check (no SPEC: N/A); an explicit policy entry
for an absent root is a FAIL (`SPEC_MISSING`). Exit codes (also
`summary.exit_code`; the same list is in `--help`): 0 every evaluated check is
PASS or N/A; 1 a violation, or a supplied impact report's required assessment is
not complete; 2 the checker could not produce a verdict (usage, dependency, I/O,
Git, policy or internal error: never a PASS or N/A; a usage error and a failure
to render the output are printed on stderr only, a failure to write it leaves
no result at all); 3 SPEC applicability
cannot be established (defined, but no SPEC scope rule produces it any more;
1 takes precedence when both apply). When it reads the index
(`--tree index`), Git itself runs the program named by the repository's own
`core.fsmonitor` setting, if any; the checker neither starts nor disables it.
Tests: `python3 -B -m unittest discover -s tests/docops -t tests/docops`.

Tooling bootstrap (added by ADR-0015 — `docs/adr/0015-tooling-bootstrap-verify-materialize.md`).
Listed in `MANIFEST.txt` and installed in a consumer by its pin. The consumers'
own `scripts/sync-tooling.sh` installers are removed by each consumer under its
own ADR (ADR-0017); until then a consumer does not run both. It is a separate
command surface from the checker above.
- `scripts/tooling.py` with `verify`, `status` and `materialize` — make a
  consumer's repository-local dependencies (`scripts/`, `schemas/` and the
  contract root `docs/tooltempest/`) reproducible in a fresh clone or a linked
  worktree, from verified Git objects at the commit pinned in
  `.tooltempest.lock`. This is the consumer target, the default. The client
  files (`skills/`, `rules/`) are installed only by the explicit client target,
  `--target client --root DIR`, into a root the operator names; there is no
  default client directory and no consumer run selects it
- `scripts/tooling_core/` — the standard-library package behind it
- `schemas/tooling-result.schema.json`, `schemas/tooling-receipt.schema.json`

`verify` and `status` are read-only, never touch the network, and report three
properties separately: local integrity (installed bytes against the hash
declared for them), upstream provenance (that hash against the blob at the
pinned commit) and freshness (the pin against a local upstream ref; `unknown`
when no ref can say). `materialize` is a dry run unless `--apply` is given;
it writes an install receipt (`.tooltempest.receipt.json`) with the source
commit and each file's hash and mode, upgrades only files it recognises as
its own earlier version, refuses to overwrite an edited managed file or an
unmanaged file, never repins, and fetches the pinned commit only with
`--allow-network` and only when it is absent locally. It installs nothing
under a client directory unless the client target is invoked with an explicit
root. Receipts are written in the lowest schema version that can hold their
paths: version 1 for `scripts/` and `schemas/`, version 2 once the contract or
client roots are installed; the reader accepts both and an older tool refuses
version 2. A root that is the top level of its own Git work tree keeps its
install lock in that repository's Git directory. Any other root (outside Git, or
inside another repository's work tree without being its top level) keeps it
inside itself (`.tooltempest-install.lock`), opened without following links; if
Git cannot say which case applies, `--apply` stops before any write. A receipt
or journal that records files of the other target (consumer or client) is
refused (`RECEIPT_OTHER_TARGET`) before any write: use a separate root for each
target. Exit codes (also
`summary.exit_code`; the same list is in `--help`): 0 for `verify` when local
integrity and provenance are both established, for `status` unless the tool
fails, and for `materialize` when the plan is executable or was applied and
verified; 1 when something is wrong or unestablished, a conflict is refused, an
installation could not be verified afterwards, or the platform cannot write
safely; 2 when the tool could not run (usage, Git, I/O or internal error, and
for `materialize --apply` a platform without POSIX file locking).
`--format json` prints a result that conforms to
`schemas/tooling-result.schema.json`; if the package itself cannot be imported,
only a minimal JSON object with `error.code` IMPORT_FAILED is printed, in any
format. Tests:
`python3 -B -m unittest discover -s tests/tooling -t tests/tooling`.

`MANIFEST.txt` is longer than the four mechanism files. By ADR-0006, ADR-0008
and ADR-0017 it lists every tracked file under `scripts/`, `schemas/`,
`skills/`, `rules/`, `docs/reference/` and `docs/tooltempest/`, so besides those
four it also holds the reference document below, the consumer contract
`docs/tooltempest/consumer-contract.md` (the text of the consumer obligation,
its single source), the universal rules `docs/tooltempest/universal-rules.md`
(the second file of the contract root), the manifest validator
`scripts/check_manifest.py` (a maintainer tool), the DocOps checker files and
the tooling bootstrap files above.

## Reference documentation (added by ADR-0008)

A separate category from the four mechanism files above:
`docs/reference/` holds shared reference documents meant to be read and
followed by a consuming project's Claude Code sessions, but **not**
copied into a client config directory the way `rules/` files are —
there is no `~/.claude/` destination for anything in this directory.
A consuming project either points to the pinned copy in this
repository, or vendors its own copy into its own repo; either way the
document is consulted in place, not installed as a rule.

- `docs/reference/documentation-rules.md` — a pointer to
  `docs/tooltempest/universal-rules.md`, which now holds the rules this
  document stated. The path stays so that links to it resolve.

See `docs/adr/0008-reference-documentation-category.md` for why this
category exists as distinct from `rules/`.

## Version identity

The canonical identity of any version of this content is the full
40-character Git commit SHA that introduced it — not a tag. Tags, if
used, are human-readable aliases only; they are not the source of
truth. When pinning or recovering a specific version, always record
and match on the full commit SHA.

## Client independence

Staying client-agnostic is an architectural commitment for this repository: it
does not and will not contain `.claude/`, `.cursor/`, or any other
client-specific directory, and it has no default client path. The differences
between consuming tools (Claude Code, and potentially others such as Cursor,
Codex or Windsurf) are handled by the operator's choice of root, not by code
here.

**Delivery of skills and rules to a client.** The pinned `skills/` and `rules/`
files are installed into a client's configuration directory (for Claude Code,
`~/.claude`) by the client target of the same materializer, ADR-0017: `python3
scripts/tooling.py materialize --target client --root DIR`, with the lock and
the receipt in that root (`.tooltempest.lock`, `.tooltempest.receipt.json`),
one pair per machine. It is selected only by that explicit argument and never
by a consumer run or a hook, it needs an explicit `--root`, and it uses the
same plan, refusal to overwrite an edited or unmanaged file, verification and
receipt as the consumer target. The checker never writes outside the
repository it inspects. A client's files that were put in place by hand or by a
consumer's own installer can differ from the pinned files; compare them with
the pinned commit (`verify --target client --root DIR`) before relying on them.

## Update model

The intended lifecycle separates two concerns:

- **Discovery** — noticing that a newer version exists — is expected
  to happen automatically.
- **Install / activation** — actually adopting a new version in a
  consuming project — is expected to always require an explicit
  action. Silent auto-update is not an acceptable behavior under this
  contract.

This is an architectural commitment for how consumers of ToolTempest
should behave. No discovery or update mechanism is implemented in
this repository itself.

## Offline recovery

If a consumer has a specific commit SHA cached locally, that cached
copy remains usable without network access. This is *not* the same as
general offline reproducibility — recovering a version you have not
previously cached still requires reaching GitHub. `scripts/tooling.py`
(above) follows this rule: it reads the pinned commit's objects from a
local cache and reaches the network only for `materialize --allow-network`
when that commit is absent.

## Usage today

Files are installed from the commit pinned in `.tooltempest.lock` by
`scripts/tooling.py materialize` (described above): the default consumer target
for `scripts/`, `schemas/` and the contract, the explicit client target for
`skills/` and `rules/`. The CLIs are `scripts/docops.py` and
`scripts/tooling.py`. What a project adopts differs by which part of the
composition it uses.

**V1 markdown primitives** — installed into your client's configuration
directory by the client target, e.g. for Claude Code:

1. Clone this repository and check out the specific commit SHA you
   intend to use (do not float on a branch).
2. Record that full commit SHA in a `.tooltempest.lock` in the client root
   (for Claude Code, `~/.claude/.tooltempest.lock`), so the exact version in
   use is reproducible later.
3. Install the files, first as a dry run and then with `--apply`:
   `python3 scripts/tooling.py materialize --target client --root ~/.claude
   --source <your clone>`. This writes `skills/spec/SKILL.md`,
   `skills/verify/SKILL.md`, `skills/doc-sync/SKILL.md` and
   `rules/drift-control.md` into that root, and refuses to overwrite a file
   it does not manage.

**V2 DocOps Protocol** — opt-in, per project, wired through local git
hooks rather than copied into a client config directory:

1. Same clone-and-pin step as above; `scripts/docops.py` is run from
   wherever the consuming project's own tooling makes it available
   (for example, `scripts/tooling.py materialize --apply` installs it with the
   other `scripts/` files of the pin).
2. The consuming project's own local git hooks call `docops.py check`
   (`--tree index` at commit time, `--push-stdin` at push time) and treat any
   non-zero exit as a block. The wiring belongs to the project; see
   `docs/adr/0013-docops-checker-spec-state-marker.md` for the checker.
3. `skills/doc-sync/SKILL.md` is reference material for reading what the
   checker reported; it needs no copying to be useful — read it directly from
   the pinned checkout or this repository, or install it with the client target.

## Connecting a new project

A step-by-step checklist for wiring up a brand-new ToolTempest
consumer. This restates "Usage today" above as a linear sequence; for
*why* each step exists, see ADR-0013
(`docs/adr/0013-docops-checker-spec-state-marker.md`) and ADR-0017
(`docs/adr/0017-distribution-model-components-fingerprint-delivery.md`) in this repository.

1. **Pin `.tooltempest.lock`.** Create (or update) the consuming
   project's `.tooltempest.lock` with the full 40-character commit SHA
   you intend to use — never a branch name or tag. See "Version
   identity" above.
2. **Initial install.** Run `python3 scripts/tooling.py materialize --apply`
   from a checkout of this repository that holds the pinned SHA (or with
   `--source`), in the consuming project: it installs `scripts/`, `schemas/`
   and the contract root files (`docs/tooltempest/consumer-contract.md` and
   `docs/tooltempest/universal-rules.md`) from the pin
   and writes a receipt. A consuming project needs no installer of its own.
3. **Activate the git hooks.** Install `pre-commit` and `pre-push` hooks
   with the consuming project's own installer (none is part of this
   repository) that call `docops.py check --tree index` and
   `docops.py check --push-stdin`, per "Usage today" above. Re-run the
   installer after every resync so `.git/hooks/` picks up any hook-source
   changes.
4. **No network in hooks.** Consumer hooks make no network calls
   (ADR-0017). ToolTempest does not provide a hook-time drift warning; a
   consumer that has one (a `git ls-remote` check in its pre-push hook) decides
   in its own ADR to remove it. Freshness is reported by `tooling.py status`
   from a local ref.
5. **Reading a block or WARNING.** Both are printed directly to the terminal
   at commit/push time by `docops.py` or by the consumer's own scripts — read
   that output first, not "ask whoever set this up":
   - A **block** (a hook exits non-zero) means `docops.py` found a violation
     (exit 1) or could not produce a verdict (exit 2, never a pass), or a
     consumer-side script failed. `docops.py report` prints the same result
     as text; `skills/doc-sync/SKILL.md` lists the finding codes.
   - A **WARNING** (any consumer-side check) never blocks anything —
     it's informational. Read the printed line for which command to run
     next (e.g. `scripts/tooling.py materialize`).

## Scope

This repository holds the four mechanism files described in
"Composition (V2)" above, shared reference documentation under
`docs/reference/` (see "Reference documentation" above, added by
ADR-0008), the source of the DocOps checker and the source of the tooling bootstrap
(see "DocOps checker" and "Tooling bootstrap" above), neither of which is part
of Composition (V2). Project-specific content (for example, a
given project's own `CHECKPOINT.md`, or its own `scripts/verify.py`)
does not belong here and is not included.
