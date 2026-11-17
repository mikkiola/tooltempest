#!/usr/bin/env python3
"""May the installed `scripts/docops.py` of a consumer run for this commit or push? (ADR-0022, Decision 5)

    tt_pin_check.py commit --root REPO [--source DIR]    before a commit: the lock in the effective index
    tt_pin_check.py push   --root REPO [--source DIR]    before a push: the lock of every pushed tip (pre-push stdin)

A consumer hook calls this before it runs the pinned checker. The answer is `decide()`: yes only if

  1. every file the lock declares, the checker, `scripts/tooling.py` and the install receipt exist in REPO;
  2. the installed `scripts/tooling.py` has the sha256 of the blob at the pinned commit, read through the
     verified object store of `--source` (default: the tooling cache), so it is not run before it is proven;
  3. every file the lock declares has the declared sha256, and the lock declares the checker;
  4. `tooling.py verify` (the whole install receipt against the pinned objects) exits 0 for that lock.

Each fact is read only if the ones before it hold. There is no fallback to another worktree or clone, nothing is
fetched, and nothing in REPO, in the lock or in the cache is written (a copy of the lock bytes lives in a
temporary directory for the duration of the `verify` call, because `verify --lock` takes a file). The path and the
hashes used are printed. A tip without `.tooltempest.lock` is not a consumer tip and is skipped with a message;
pushing tips that pin different locks is refused, because one installed checker cannot match both.

Limit: this script and the `tooling_core` modules it imports are installed files too. They are covered by step 4
(the receipt check hashes them against the pinned blobs) but not before they run; a copy altered to lie is outside
the accidental-staleness threat model of ADR-0022.

Exit 0: passed, or skipped. Exit 1: blocked. Exit 2: usage or internal failure, which blocks as well.
"""
import argparse
import hashlib
import os
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tooling_core.declarations import FormatError, parse_lock  # noqa: E402
from tooling_core.engine import DEFAULT_SOURCE  # noqa: E402
from tooling_core.objects import MODE_FILES, ObjectError, ObjectStore, git_env, run_git  # noqa: E402

LOCK, CHECKER, TOOLING, RECEIPT = ".tooltempest.lock", "scripts/docops.py", "scripts/tooling.py", ".tooltempest.receipt.json"


class Block(Exception):
    pass


def say(msg):
    print("[tt-pin] " + msg)


def decide(lock, present, tooling, files, receipt):
    """The whole decision: 'skip' without a lock, else the first fact that does not hold, else 'pass'.
    The order is the order of reading: a later fact is read only when every earlier one holds."""
    if not lock:
        return "skip"
    for holds, verdict in ((present, "missing"), (tooling, "tooling"), (files, "files"), (receipt, "receipt")):
        if not holds:
            return verdict
    return "pass"


def sha256_file(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def pinned_sha256(source, commit, path):
    store = ObjectStore(source)
    try:
        entry = store.resolve(commit, path)
        return hashlib.sha256(store.read_blob(entry.oid)).hexdigest() if entry else None
    finally:
        store.close()


def verify_receipt(root, raw, source):
    """(exit code of `tooling.py verify` for this lock, its two summary lines)."""
    with tempfile.TemporaryDirectory(prefix="tt-pin-") as tmp:
        lock_file = os.path.join(tmp, "lock.json")
        with open(lock_file, "wb") as handle:
            handle.write(raw)
        proc = subprocess.run([sys.executable, "-I", os.path.join(root, TOOLING), "verify", "--root", root, "--lock", lock_file,
                               "--source", source, "--format", "text"], capture_output=True, text=True, timeout=120,
                              stdin=subprocess.DEVNULL, env=git_env())
    lines = [l.strip() for l in proc.stdout.splitlines() if l.strip().startswith(("local integrity", "upstream provenance"))]
    return proc.returncode, "; ".join(lines)


def judge(root, lock, raw, source):
    """The verdict of decide() for a lock (None: no lock; `raw` are its bytes) and the reason of each verdict
    that is not 'pass'. Each fact is read only if every fact before it holds."""
    if lock is None:
        return decide(False, False, False, False, False), {}
    why = {}
    where = {p: os.path.join(root, p) for p in sorted(set(lock.declared) | {CHECKER, TOOLING, RECEIPT})}
    gone = [f for f in where.values() if os.path.islink(f) or not os.path.isfile(f)]
    present = not gone
    why["missing"] = "%s is missing or not a regular file" % ", ".join(gone)
    tooling = files = receipt = False
    if present:
        have, pinned = sha256_file(where[TOOLING]), None
        why["tooling"] = "the installed %s differs from the pinned one" % TOOLING
        try:
            pinned = pinned_sha256(source, lock.commit, TOOLING)
        except ObjectError as exc:
            why["tooling"] = "cannot read the pinned %s from %s: %s" % (TOOLING, source, exc)
        say("%s sha256 %s, pinned commit %s has %s" % (where[TOOLING], have, lock.commit[:12], pinned))
        tooling = have == pinned
    if tooling:
        checked = sorted(set(lock.declared) | {CHECKER})
        for path in checked:
            say("%s sha256 %s, lock declares %s" % (where[path], sha256_file(where[path]), lock.declared.get(path)))
        bad = [p for p in checked if lock.declared.get(p) != sha256_file(where[p])]
        files = not bad
        why["files"] = "%s differs from the lock, or the lock does not declare it" % ", ".join(bad)
    if files:
        code, summary = verify_receipt(root, raw, source)
        receipt = code == 0
        why["receipt"] = "`tooling.py verify` exited %d (%s)" % (code, summary)
    return decide(True, present, tooling, files, receipt), why


def check_lock(root, label, raw, source):
    try:
        lock = None if raw is None else parse_lock(raw)
    except FormatError as exc:
        raise Block("%s: the lock is not usable: %s" % (label, exc.message))
    verdict, why = judge(root, lock, raw, source)
    if verdict == "skip":
        say("%s has no %s: not a consumer tip, verification skipped" % (label, LOCK))
    elif verdict == "pass":
        say("%s: verified, the install receipt matches the pinned commit (verify exit 0)" % label)
    else:
        raise Block("%s: %s. To fix: python3 <ToolTempest checkout>/scripts/tooling.py materialize --root %s --source %s "
                    "--apply (add --allow-network to fetch exactly %s)" % (label, why[verdict], root, source, lock.commit))


def lock_in_index(root, store):
    """The bytes of the lock in the effective index of the commit in progress (GIT_INDEX_FILE is the only
    variable kept from the hook environment, so `commit -a` and `--amend` see the right index), or None."""
    extra = {"GIT_INDEX_FILE": os.environ["GIT_INDEX_FILE"]} if "GIT_INDEX_FILE" in os.environ else None
    # core.fsmonitor=false: reading the index must not start the program or daemon a clone's config names
    out = run_git(["-c", "core.fsmonitor=false", "ls-files", "-s", "--", LOCK], cwd=root, env=git_env(extra))
    out = out.stdout.decode("utf-8", "replace")
    entries = [line.split()[:3] for line in out.splitlines()]
    if not entries:
        return None
    if len(entries) != 1 or entries[0][2] != "0" or entries[0][0] not in MODE_FILES:
        raise Block("%s is unmerged, or is a symlink or a submodule, in the index" % LOCK)
    return store.read_blob(entries[0][1])


def lock_at_tip(store, sha):
    kind, body = store.read_object(sha)
    while kind == "tag":  # an annotated tag is checked through the object it names
        first = body.split(b"\n", 1)[0].decode("ascii", "replace")
        sha = first[len("object "):] if first.startswith("object ") else first
        kind, body = store.read_object(sha)
    entry = store.resolve(sha, LOCK)
    return store.read_blob(entry.oid) if entry else None


def run(mode, root, stdin, source):
    store = ObjectStore(root)
    try:
        if mode == "commit":
            check_lock(root, "index", lock_in_index(root, store), source)
            return 0
        lines = [line.split() for line in stdin.read().splitlines() if line.strip()]
        if any(len(fields) != 4 for fields in lines):
            raise Block("the input is not a pre-push line (<local ref> <local oid> <remote ref> <remote oid>)")
        tips = [(f[0], f[1]) for f in lines if f[1].strip("0")]  # a deletion (all-zero local oid) has nothing to check
        if not tips:
            say("nothing to push that needs a check (no updated tip)")
        groups = {}
        for ref, sha in tips:
            raw = lock_at_tip(store, sha)
            if raw is None:
                check_lock(root, "%s (%s)" % (ref, sha[:12]), None, source)
            else:
                groups.setdefault(raw, []).append(ref)
        if len(groups) > 1:
            raise Block("the pushed tips pin different locks (%s). One installed checker cannot match them: push them "
                        "separately" % "; ".join(",".join(refs) for refs in groups.values()))
        for raw, refs in groups.items():
            check_lock(root, ",".join(refs), raw, source)
        return 0
    finally:
        store.close()


def main(argv):
    parser = argparse.ArgumentParser(prog="tt_pin_check.py", description=__doc__.split("\n")[0])
    parser.add_argument("mode", choices=("commit", "push"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse: 2 for a usage error, 0 for --help
        return exc.code
    try:
        return run(args.mode, os.path.realpath(args.root), sys.stdin, os.path.abspath(os.path.expanduser(args.source)))
    except Block as exc:
        say("BLOCKED: %s" % exc)
        return 1
    except Exception as exc:  # an internal failure blocks as well
        say("BLOCKED (internal error): %s: %s" % (type(exc).__name__, exc))
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
