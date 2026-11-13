# ADR-0022: Normative Content and Gate Executables Are Read From the Pinned Commit Through a Verified Object Store

Status: Accepted by the Owner, 2026-11-12. It supersedes no ADR. It extends the principle of ADR-0015 (pin by commit, verify by hash, read Git objects, never a working tree) from the installer to every gate and hook that relies on pinned content.

## Context & Constraints

Two incidents on 2026-11-12 (Owner audit, not reproduced here) had one cause: a tool read normative content or an executable from a mutable local copy instead of the pinned commit.

- A user-level gate demanded a read of the rules at a fixed path in a local clone of this repository. The clone was behind the pin, so the gate forced a read of a superseded document, and it accepted any read of that path whatever the bytes were.
- A consumer pre-commit hook fell back to the checker in the consumer's main worktree. The installed checker differed from the hash in the lock, and no hook compared the two.

Observations, git 2.44.0, scratch repositories, 2026-11-12. The reproductions are fixtures of the user-level gate, not files of this repository:

- `git cat-file blob`, `git show <commit>:<path>` and `git cat-file --batch` return exit 0 with the bytes of a corrupted blob. Some tree and commit forms also return a substituted object with exit 0. A plain Git read is therefore not a hash check.
- `GIT_DIR`, `GIT_OBJECT_DIRECTORY`, `objects/info/alternates` and `refs/replace` change what a plain read returns.
- `GIT_NO_LAZY_FETCH=1` does not stop a promisor clone from fetching a missing object. `GIT_ALLOW_PROTOCOL=none` does.
- `scripts/tooling_core/objects.py` rehashes every object of the chain commit, tree, blob against its name, clears `GIT_*` and disables replace refs: `git show a55d6c0:scripts/tooling_core/objects.py | wc -l` prints 312.

Constraints: the Owner accepted the threat model of 2026-11-12. In scope are accidental staleness of clones and installed copies, accidental cache corruption, accidental environment inheritance, and accidental races. A same-user actor who deliberately edits hooks, settings or transcripts is out of scope. A repository without `.tooltempest.lock` is not a consumer: it is not gated. This repository, the source of the pin, uses its own `HEAD` as the pin.

## Decision

Normative content and the executables that a gate or hook relies on are taken from the objects of the commit pinned in the lock of the repository where the work happens, and from nowhere else.

1. The rules that govern an edit come from the `HEAD` of the repository that holds the edited file. The executable that a commit runs comes from the effective index of the commit in progress. The lock itself is read as a pinned object, not as a file.
2. Objects are read only by a verifier that rehashes the whole chain, ignores inherited Git environment and replace refs, and never contacts a network. The verifier is a vendored copy of `scripts/tooling_core/objects.py`. The hook compares its sha256 with a constant before it executes the bytes it read. Drift of the copy is detected against the current source of this repository.
3. A gate accepts a read as evidence by content: the bytes the reader received must equal the verified blob. The path of the read does not matter. A document that a read cannot reproduce byte for byte cannot be evidence, and the gate refuses it with that reason.
4. Any failure of a gate (internal error, missing object, mismatch, unreadable input) denies. A gate does not fetch and does not write. A missing object denies with the exact command that fetches the pinned commit.
5. A pinned executable runs only after the installed file set matches its install receipt and the pinned objects. There is no fallback to another worktree or clone, and the hook prints the path and hashes it used.
6. The known patterns of this defect class are covered by a static scan with fixtures. The scan runs by hand on real hook files, and CI runs only its fixture tests; it does not enforce anything by itself. A clone that is behind, dirty or detached is reported as a warning and never blocks.

## Alternatives & Rationale

| Option | Why rejected |
|---|---|
| Keep the clone current and read it | Currency of a mutable copy cannot be enforced. A clone behind its remote was the incident. |
| Plain `git show <pin>:<path>` | Exit 0 on corrupted blobs, and the result depends on inherited environment, alternates and replace refs. |
| Import the verifier from a clone or a consumer | Executes code from a mutable tree: the defect itself. |
| Re-implement a small verifier in each hook | A second copy of tested code. WR-8 asks to reuse the existing one. |
| Read-only snapshot of the pinned file as the verifier | The protection is advisory for the same user, and the chain is not verified at read time. It remains useful as a file for the reader to open. |
| Compare the path of the read | Accepts a stale file at the right path and rejects the right bytes at another path. |

## Consequences

- A document with CRLF line ends, a byte order mark or invalid UTF-8 cannot be proven by a read, so a gate that requires it will refuse. The current target document is plain UTF-8 with LF.
- The vendored verifier is a second copy of one file. Its update is a deliberate step, and the drift check names it.
- The gate is a guardrail against forgetting, not a boundary against bypass. Writes through an interpreter, Git itself, `curl`, `tar` or a variable destination are not recognized, and the scan lists the forms it does not cover. The pre-commit checks of the consumers remain the second layer.
- A tip without a lock, such as a data-only branch, is skipped by the consumer hooks with a printed message. This matches the rule for repositories without a lock.
- This record changes no consumer. A consumer receives the hook change only through its own repin and its own delivery decision.

## Confirmation & Revisit

Confirmed by the fixture suite of the scan (`python3 -B -m unittest discover -s tests/mutable_reads -t tests/mutable_reads`), by the offline gate tests of the user-level hooks, and by live tests of the hook failure policy. Revisit when the representation of a read result in the client changes, when `objects.py` changes its guarantees, when a Git release changes lazy-fetch semantics, or when a case shows that the accidental-only threat model is too narrow.

## Source

The Owner's decisions of 2026-11-12 in the session that built the gate, and an independent technical review of the plan by an external AI on the same day. The review was treated as input: each of its findings was reproduced before it changed the plan.
