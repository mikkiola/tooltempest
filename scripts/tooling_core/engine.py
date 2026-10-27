"""verify / status / materialize.

`evaluate` is read-only and shared by all three commands. It reports three
independent properties, never collapsed into one verdict:

  local integrity      installed bytes == the hash declared for them
  upstream provenance  that declared hash == the verified blob at the pinned commit
  freshness            the pin is the newest commit of a local upstream ref

A hash is "declared" by the lock (its `path`/`sha256` and optional `files`)
or, failing that, recorded in the install receipt. Bytes are only ever taken
from hash-verified Git objects at the exact pinned commit (see objects.py).

`materialize` plans first (dry-run is the default), refuses the whole
operation on any conflict, and only with `apply=True` writes: stage every
file beside its destination, write an `installing` receipt (the journal),
rename the staged files into place, write the `complete` receipt, then
re-verify. A crash between journal and completion leaves the journal, which
`verify`/`status` report as recovery-required and a repeated
`materialize --apply` finishes. Every write, rename and removal goes through
directory descriptors held for the whole transaction (see fsops.py), never a path
string; only a file or directory this run created is ever removed, and only while
it is still what the run made. Nothing is repinned; no path outside the consumer's
scripts/ and schemas/ is touched.
"""
from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import RESULT_SCHEMA_VERSION, TOOL_NAME, TOOL_VERSION, fsops
from .declarations import (FormatError, Lock, Manifest, Receipt, ReceiptFile, parse_lock,
                           parse_manifest, parse_receipt, path_problem, serialize_receipt)
from .objects import (MODE_FILES, MODE_GITLINK, MODE_SYMLINK, MODE_TREE, GitFailure,
                      GitMissing, ObjectCorrupt, ObjectMissing, ObjectStore, TreeEntry,
                      fetch_commit, fetch_url_problem, run_git)

DEFAULT_SOURCE = "~/.cache/tooltempest/repo"
STAGE_PREFIX = ".tooltempest-stage."
DEFAULT_UPSTREAM_REFS = ("refs/remotes/origin/main", "refs/heads/main")


class ToolFailure(Exception):
    """The tool could not run to an answer (usage, Git, I/O, internal). Exit 2."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Options:
    root: str
    lock_path: str
    receipt_path: str
    source: str
    upstream_ref: Optional[str] = None
    allow_network: bool = False
    apply: bool = False
    invoked_as: str = "scripts/tooling.py"


@dataclass
class Dest:
    kind: str  # "absent" | "file" | "unsafe"
    sha256: Optional[str] = None
    mode: Optional[str] = None
    problem: Optional[str] = None


@dataclass
class Item:
    path: str
    dest: Dest
    entry: Optional[TreeEntry] = None  # the entry at the pinned commit
    data: Optional[bytes] = None  # verified blob bytes
    pinned_sha256: Optional[str] = None
    source_problem: Optional[str] = None
    local: str = "unknown"
    provenance: str = "unknown"
    expected: Optional[str] = None
    expected_by: Optional[str] = None
    action: Optional[str] = None
    reason: Optional[str] = None


@dataclass
class Evaluation:
    opts: Options
    findings: List[dict] = field(default_factory=list)  # about lock, source, provenance
    state_findings: List[dict] = field(default_factory=list)  # about the installed files
    lock: Optional[Lock] = None
    receipt: Optional[Receipt] = None
    receipt_raw: Optional[bytes] = None
    store: Optional[ObjectStore] = None
    commit_present: bool = False
    manifest: Optional[Manifest] = None
    items: List[Item] = field(default_factory=list)
    freshness: dict = field(default_factory=dict)
    source_info: dict = field(default_factory=dict)
    network_used: bool = False

    def find(self, code: str, severity: str, message: str, path: Optional[str] = None,
             hint: Optional[str] = None, state: bool = False) -> None:
        item = {"code": code, "severity": severity, "path": path, "message": message, "hint": hint}
        (self.state_findings if state else self.findings).append(item)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# -- the consumer's files --------------------------------------------------------------

def inspect_dest(root: str, rel: str) -> Dest:
    current = root
    parts = rel.split("/")
    for part in parts[:-1]:
        current = os.path.join(current, part)
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            return Dest("absent")
        except OSError as exc:
            return Dest("unsafe", problem="cannot inspect %s: %s" % (current, exc.strerror))
        if stat.S_ISLNK(info.st_mode):
            return Dest("unsafe", problem="a symlink is in the way: %s" % os.path.relpath(current, root))
        if not stat.S_ISDIR(info.st_mode):
            return Dest("unsafe", problem="a non-directory is in the way: %s" % os.path.relpath(current, root))
    return _classify_final(os.path.join(current, parts[-1]), None, rel)


def inspect_in(directory: "fsops.Dir", name: str, rel: str) -> Dest:
    """The same classification of one destination, relative to a held directory."""
    return _classify_final(name, directory.fd, rel)


def _classify_final(final: str, dir_fd: Optional[int], rel: str) -> Dest:
    try:
        info = os.stat(final, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return Dest("absent")
    except OSError as exc:
        return Dest("unsafe", problem="cannot inspect %s: %s" % (rel, exc.strerror))
    if stat.S_ISLNK(info.st_mode):
        return Dest("unsafe", problem="the destination is a symlink")
    if not stat.S_ISREG(info.st_mode):
        return Dest("unsafe", problem="the destination is not a regular file")
    digest = hashlib.sha256()
    try:
        fd = os.open(final, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd)
        with os.fdopen(fd, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError as exc:
        return Dest("unsafe", problem="cannot read %s: %s" % (rel, exc.strerror))
    mode = "100755" if info.st_mode & stat.S_IXUSR else "100644"
    return Dest("file", digest.hexdigest(), mode)


def _read_small_file(path: str, what: str) -> Optional[bytes]:
    """The bytes of a regular, non-symlink file, or None if it does not exist."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ToolFailure("IO_ERROR", "cannot inspect %s: %s" % (what, exc.strerror))
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise FormatError("%s_MALFORMED" % what.split()[-1].upper(),
                          "%s must be a regular file, not a symlink or directory" % what)
    try:
        with open(path, "rb") as handle:
            return handle.read(1024 * 1024 + 1)
    except OSError as exc:
        raise ToolFailure("IO_ERROR", "cannot read %s: %s" % (what, exc.strerror))


# -- evaluation (read-only) -----------------------------------------------------------

def _command_hint(opts: Options, command: str, extra: str = "") -> str:
    parts = ["python3", opts.invoked_as, command, "--root", opts.root]
    if opts.source != os.path.abspath(os.path.expanduser(DEFAULT_SOURCE)):
        parts += ["--source", opts.source]
    return " ".join(parts) + (" " + extra if extra else "")


def evaluate(opts: Options, allow_fetch: bool = False) -> Evaluation:
    ev = Evaluation(opts)
    try:
        raw = _read_small_file(opts.lock_path, "the lock")
        if raw is None:
            ev.find("LOCK_MISSING", "error", "no lock file at %s: nothing is pinned" % opts.lock_path,
                    hint="create .tooltempest.lock with a full commit id; this tool never pins")
            return ev
        ev.lock = parse_lock(raw)
    except FormatError as exc:
        ev.find(exc.code, "error", exc.message)
        return ev

    try:
        raw_receipt = _read_small_file(opts.receipt_path, "the install receipt")
        if raw_receipt is not None:
            ev.receipt_raw = raw_receipt
            ev.receipt = parse_receipt(raw_receipt)
    except FormatError as exc:
        ev.find(exc.code, "error", exc.message, hint="a malformed receipt is never overwritten; "
                "inspect it, then move it aside to start again")
        return ev

    try:
        _open_source(ev, allow_fetch)
        manifest_known = _read_manifest(ev)
        _build_items(ev, manifest_known)
        _judge(ev)
        _freshness(ev)
    finally:
        if ev.store is not None:
            ev.store.close()
    return ev


def _open_source(ev: Evaluation, allow_fetch: bool) -> None:
    lock, opts = ev.lock, ev.opts
    ev.source_info = {"path": opts.source, "available": False, "remote_url": None,
                      "remote_matches_lock": None}
    for attempt in (0, 1):
        if ev.store is not None:
            ev.store.close()
        try:
            ev.store = ObjectStore(opts.source)
        except GitMissing as exc:
            raise ToolFailure("GIT_MISSING", str(exc))
        except GitFailure:
            ev.store = None
        if ev.store is not None:
            try:
                ev.store.commit_tree(lock.commit)
                ev.commit_present = True
                break
            except ObjectMissing:
                pass
            except ObjectCorrupt as exc:
                ev.find("OBJECT_CORRUPT", "error", str(exc),
                        hint="the source object store is damaged; use a different --source")
                break
            except GitFailure as exc:
                raise ToolFailure("GIT_FAILED", str(exc))
        if attempt == 0 and allow_fetch:
            problem = fetch_url_problem(lock.repository)
            if problem:
                ev.find("FETCH_REFUSED", "error", problem)
                break
            try:
                fetch_commit(opts.source, lock.repository, lock.commit)
                ev.network_used = True
            except GitFailure as exc:
                ev.network_used = True
                ev.find("FETCH_FAILED", "error", "fetching %s from %s failed: %s"
                        % (lock.commit, lock.repository, exc),
                        hint="check the repository URL and your connection, or provide --source "
                        "with the commit already present")
                break
        else:
            break
    if ev.store is None:
        ev.find("SOURCE_UNAVAILABLE", "error",
                "no Git object store at %s, so the pinned commit cannot be read" % opts.source,
                hint="pass --source PATH to a ToolTempest repository holding the pinned commit; "
                "materialize --apply --allow-network may fetch it")
        return
    ev.source_info["available"] = True
    try:
        url = ev.store.remote_url()
        ev.source_info["remote_url"] = url
        if url is not None:
            ev.source_info["remote_matches_lock"] = _same_remote(url, ev.lock.repository)
            if ev.source_info["remote_matches_lock"] is False:
                ev.find("SOURCE_REMOTE_MISMATCH", "warning",
                        "the source's remote (%s) differs from the lock's repository (%s); bytes "
                        "are still taken only from the verified pinned commit" % (url, ev.lock.repository))
    except GitFailure:
        pass
    if not ev.commit_present and not any(f["severity"] == "error" for f in ev.findings):
        ev.find("PIN_OBJECT_MISSING", "error",
                "the pinned commit %s is not present in %s" % (lock.commit, opts.source),
                hint="nothing was fetched. Provide a --source that has it, or run "
                "materialize --apply --allow-network to fetch exactly this commit "
                "(a dry run never fetches)")


def _same_remote(first: str, second: str) -> bool:
    def norm(url: str) -> str:
        url = url.strip().rstrip("/")
        return url[:-4] if url.endswith(".git") else url
    return norm(first) == norm(second)


def _read_manifest(ev: Evaluation) -> bool:
    if not ev.commit_present:
        return False
    try:
        entry = ev.store.resolve(ev.lock.commit, "MANIFEST.txt")
        if entry is None or entry.mode not in MODE_FILES:
            ev.find("MANIFEST_MISSING", "error",
                    "MANIFEST.txt is not a regular file at the pinned commit %s" % ev.lock.commit)
            return False
        ev.manifest = parse_manifest(ev.store.read_blob(entry.oid))
        return True
    except FormatError as exc:
        ev.find(exc.code, "error", "MANIFEST.txt at the pinned commit: " + exc.message)
    except ObjectMissing as exc:
        ev.find("OBJECT_MISSING", "error", str(exc),
                hint="the source is incomplete; provide a full --source or use --allow-network")
    except ObjectCorrupt as exc:
        ev.find("OBJECT_CORRUPT", "error", str(exc), hint="the source object store is damaged")
    except GitFailure as exc:
        raise ToolFailure("GIT_FAILED", str(exc))
    return False


def _build_items(ev: Evaluation, manifest_known: bool) -> None:
    lock, receipt = ev.lock, ev.receipt
    if manifest_known:
        names = list(ev.manifest.install)
        for path in sorted(set(lock.declared) - set(names)):
            ev.find("DECLARED_PATH_NOT_INSTALLABLE", "error",
                    "the lock declares a hash for %s, which the pinned manifest does not install" % path,
                    path=path)
    else:
        names = sorted(set(lock.declared) | (set(receipt.files) if receipt else set()))
    for path in names:
        problem = path_problem(path)
        item = Item(path, inspect_dest(ev.opts.root, path) if not problem else Dest("unsafe", problem=problem))
        if manifest_known:
            _read_source(ev, item)
        ev.items.append(item)


def _read_source(ev: Evaluation, item: Item) -> None:
    code = None
    try:
        entry = ev.store.resolve(ev.lock.commit, item.path)
        if entry is None:
            code, item.source_problem = "SOURCE_FILE_MISSING", "absent at the pinned commit"
        elif entry.mode not in MODE_FILES:
            kind = {MODE_SYMLINK: "a symlink", MODE_GITLINK: "a submodule", MODE_TREE: "a directory"}
            code = "SOURCE_FILE_UNSUPPORTED"
            item.source_problem = "unsupported source type: %s (mode %s)" % (
                kind.get(entry.mode, "an unknown object"), entry.mode)
        else:
            item.entry = entry
            item.data = ev.store.read_blob(entry.oid)
            item.pinned_sha256 = sha256_hex(item.data)
    except ObjectMissing as exc:
        code, item.source_problem = "OBJECT_MISSING", str(exc)
    except ObjectCorrupt as exc:
        code, item.source_problem = "OBJECT_CORRUPT", str(exc)
    except GitFailure as exc:
        raise ToolFailure("GIT_FAILED", str(exc))
    if code:
        item.entry = None
        item.data = None
        item.pinned_sha256 = None
        ev.find(code, "error", "%s: %s" % (item.path, item.source_problem), path=item.path)


def _judge(ev: Evaluation) -> None:
    lock, receipt = ev.lock, ev.receipt
    same_commit = receipt is not None and receipt.commit == lock.commit
    if receipt is not None and receipt.state == "installing":
        ev.find("RECOVERY_REQUIRED", "error",
                "an earlier materialize did not finish (the receipt is a journal for commit %s)"
                % receipt.commit,
                hint="run: " + _command_hint(ev.opts, "materialize", "--apply")
                + " to finish it; files that match neither version are left untouched", state=True)
    undeclared = 0
    for item in ev.items:
        declared = lock.declared.get(item.path)
        rec = receipt.files.get(item.path) if receipt else None
        # a record from an earlier commit still vouches for a file that did not change since
        rec_applies = rec is not None and (same_commit or (
            item.entry is not None and rec.sha256 == item.pinned_sha256 and rec.mode == item.entry.mode))
        item.expected, item.expected_by = (declared, "lock") if declared else (
            (rec.sha256, "receipt") if rec_applies else (None, None))
        item.local = _local_status(item, declared, rec, receipt, rec_applies)
        item.provenance = _provenance(item)
        if (item.provenance == "verified" and item.expected_by == "receipt" and item.entry is not None
                and rec.git_blob != item.entry.oid):
            item.provenance = "mismatch"
        _local_findings(ev, item)
        if item.provenance == "mismatch":
            ev.find("PROVENANCE_MISMATCH", "error",
                    "%s: what the %s declares (%s) is not the blob at the pinned commit (%s)"
                    % (item.path, item.expected_by, item.expected, item.pinned_sha256),
                    path=item.path, hint="the declaration or the pin is wrong; fix the lock "
                    "deliberately. This tool never repins")
        elif item.provenance == "undeclared":
            undeclared += 1
    if undeclared:
        ev.find("PROVENANCE_UNESTABLISHED", "error",
                "%d file(s) have no declared hash (lock) or receipt record to compare with the "
                "pinned blob" % undeclared,
                hint="run: " + _command_hint(ev.opts, "materialize", "--apply")
                + " to record them (no bytes change when they already equal the pin)", state=True)


def _local_status(item: Item, declared: Optional[str], rec: Optional[ReceiptFile],
                  receipt: Optional[Receipt], rec_applies: bool) -> str:
    dest = item.dest
    if dest.kind == "unsafe":
        return "unsafe"
    if dest.kind == "absent":
        return "missing"
    if receipt is not None and receipt.state == "installing" and rec is not None:
        if (dest.sha256, dest.mode) == (rec.sha256, rec.mode):
            return "ok" if declared in (None, dest.sha256) else "modified"
        if rec.previous_sha256 is not None and (dest.sha256, dest.mode) == (rec.previous_sha256, rec.previous_mode):
            return "stale"
        return "modified"
    if declared:
        if dest.sha256 == declared:
            # the declaration has no mode: the receipt's, else the verified pin's, decides
            if rec and rec_applies and rec.sha256 == declared:
                wanted = rec.mode
            elif item.entry is not None and item.pinned_sha256 == declared:
                wanted = item.entry.mode
            else:
                wanted = None
            if wanted is not None and dest.mode != wanted:
                return "modified"
            return "ok"
        if rec and not rec_applies and dest.sha256 == rec.sha256:
            return "outdated"
        return "modified"
    if rec:
        if dest.sha256 == rec.sha256 and dest.mode == rec.mode:
            return "ok" if rec_applies else "outdated"
        return "modified"
    return "unrecorded"


def _provenance(item: Item) -> str:
    if item.pinned_sha256 is None:
        return "unknown"
    if item.expected is None:
        return "undeclared"
    return "verified" if item.expected == item.pinned_sha256 else "mismatch"


_LOCAL_TEXT = {
    "missing": ("DEPENDENCY_MISSING", "is required by the pin but is not installed"),
    "modified": ("LOCAL_MODIFIED", "differs from the hash declared for it (edited since install?)"),
    "outdated": ("INSTALL_BEHIND_PIN", "is a known-managed earlier version, not the pinned one"),
    "stale": ("RECOVERY_REQUIRED_FILE", "still has its pre-install bytes (the installation was interrupted)"),
    "unrecorded": ("UNRECORDED_FILE", "has no declared hash or receipt record to check it against"),
    "unsafe": ("PATH_UNSAFE", "cannot be used"),
}


def _local_findings(ev: Evaluation, item: Item) -> None:
    if item.local not in _LOCAL_TEXT:
        return
    code, text = _LOCAL_TEXT[item.local]
    if item.local == "unsafe":
        text = "cannot be used: %s" % item.dest.problem
    hint = None
    if item.local in ("missing", "outdated", "stale"):
        hint = "run: " + _command_hint(ev.opts, "materialize", "--apply")
    elif item.local == "modified":
        hint = ("materialize will not overwrite it; keep or move your edit, then restore the "
                "managed bytes deliberately")
    if code == "RECOVERY_REQUIRED_FILE":
        return  # reported once, as RECOVERY_REQUIRED
    ev.find(code, "error", "%s %s" % (item.path, text), path=item.path, hint=hint, state=True)


def _freshness(ev: Evaluation) -> None:
    fresh = {"status": "unknown", "upstream_ref": None, "upstream_commit": None, "behind": None,
             "reason": None}
    ev.freshness = fresh
    if ev.store is None or not ev.commit_present:
        fresh["reason"] = "the pinned commit is not available locally, so nothing can be compared"
        return
    candidates = [ev.opts.upstream_ref] if ev.opts.upstream_ref else list(DEFAULT_UPSTREAM_REFS)
    try:
        for ref in candidates:
            tip = ev.store.ref_commit(ref)
            if tip:
                fresh["upstream_ref"], fresh["upstream_commit"] = ref, tip
                break
        if not fresh["upstream_commit"]:
            fresh["reason"] = ("no upstream ref (%s) in the source; nothing was fetched, so "
                               "freshness is not known" % ", ".join(candidates))
            ev.find("FRESHNESS_UNKNOWN", "info", fresh["reason"])
            return
        tip, pin = fresh["upstream_commit"], ev.lock.commit
        if tip == pin:
            fresh["status"] = "current"
        elif ev.store.is_ancestor(pin, tip):
            fresh["status"] = "behind"
            fresh["behind"] = ev.store.count_between(pin, tip)
            ev.find("PIN_BEHIND", "warning", "the pin is %d commit(s) behind %s as last fetched into the "
                    "source; repinning is a separate, explicit decision" % (fresh["behind"], fresh["upstream_ref"]))
        else:
            fresh["status"] = "not_on_ref"
            fresh["reason"] = "the pin is not an ancestor of %s" % fresh["upstream_ref"]
            ev.find("PIN_NOT_ON_UPSTREAM_REF", "info", fresh["reason"])
    except GitFailure as exc:
        fresh["status"], fresh["reason"] = "unknown", str(exc)


# -- result document ----------------------------------------------------------------------

def properties(ev: Evaluation) -> Tuple[str, str]:
    structural = any(f["severity"] == "error" for f in ev.findings)
    locals_ = [item.local for item in ev.items]
    if not ev.items and structural:  # nothing could be judged (this includes: no usable lock)
        return "unknown", "unknown"
    if any(s in ("missing", "modified", "outdated", "stale", "unsafe") for s in locals_):
        local = "failed"
    elif any(s == "unrecorded" for s in locals_) or structural and not ev.manifest:
        local = "unknown"
    else:
        local = "ok"
    provs = [item.provenance for item in ev.items]
    if any(p == "mismatch" for p in provs):
        provenance = "failed"
    elif any(p in ("unknown", "undeclared") for p in provs) or structural and not ev.manifest:
        provenance = "unknown"
    else:
        provenance = "ok"
    return local, provenance


def document(ev: Evaluation, command: str, extra: Optional[dict] = None) -> dict:
    local, provenance = properties(ev)
    findings = ev.findings + ev.state_findings
    errors = [f for f in findings if f["severity"] == "error"]
    receipt = ev.receipt
    doc = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "command": command,
        "root": ev.opts.root,
        "pin": ({"repository": ev.lock.repository, "commit": ev.lock.commit} if ev.lock else None),
        "source": dict(ev.source_info) if ev.source_info else None,
        "network": {"allowed": bool(ev.opts.allow_network), "used": ev.network_used},
        "receipt": ({"present": True, "state": receipt.state, "commit": receipt.commit,
                     "matches_pin": bool(ev.lock and receipt.commit == ev.lock.commit)}
                    if receipt else {"present": False, "state": None, "commit": None,
                                     "matches_pin": None}),
        "summary": {
            "local_integrity": local,
            "upstream_provenance": provenance,
            "freshness": ev.freshness.get("status", "unknown") if ev.freshness else "unknown",
            "errors": len(errors),
            "exit_code": 0,
        },
        "freshness": ev.freshness or None,
        "files": [_file_record(item) for item in ev.items],
        "skipped": [{"path": p, "reason": r} for p, r in (ev.manifest.skipped if ev.manifest else [])],
        "findings": findings,
        "error": None,
    }
    if extra:
        doc.update(extra)
    if command == "verify":
        doc["summary"]["exit_code"] = 0 if (local == "ok" and provenance == "ok" and not errors) else 1
    elif command == "status":
        doc["summary"]["exit_code"] = 0
    return doc


def _file_record(item: Item) -> dict:
    return {
        "path": item.path,
        "local": {"status": item.local, "sha256": item.dest.sha256, "mode": item.dest.mode,
                  "problem": item.dest.problem},
        "declared": {"sha256": item.expected, "by": item.expected_by},
        "provenance": item.provenance,
        "pinned": ({"sha256": item.pinned_sha256, "git_blob": item.entry.oid, "mode": item.entry.mode}
                   if item.entry and item.pinned_sha256 else None),
        "matches_pin": (item.dest.sha256 == item.pinned_sha256 and item.dest.mode == item.entry.mode
                        if item.dest.kind == "file" and item.entry and item.pinned_sha256 else None),
        "action": item.action,
        "reason": item.reason,
    }


def error_document(opts: Options, command: str, code: str, message: str, apply: bool = False) -> dict:
    """The result document of a run that could not produce a verdict (exit 2). The schema
    requires `result` for materialize, so it is present there: nothing was applied, and
    `dry_run` says whether --apply was asked for (opts is None when the failure came
    before the options could be built)."""
    doc = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "command": command,
        "root": opts.root if opts else None,
        "pin": None, "source": None,
        "network": {"allowed": bool(opts and opts.allow_network), "used": False},
        "receipt": {"present": False, "state": None, "commit": None, "matches_pin": None},
        "summary": {"local_integrity": "unknown", "upstream_provenance": "unknown",
                    "freshness": "unknown", "errors": 1, "exit_code": 2},
        "freshness": None, "files": [], "skipped": [], "findings": [],
        "error": {"code": code, "message": message},
    }
    if command == "materialize":
        doc["result"] = {"dry_run": not (opts.apply if opts is not None else apply), "applied": False,
                         "written": [], "receipt_written": False, "post_verification": None}
    return doc


# -- materialize ----------------------------------------------------------------------------

def _plan(ev: Evaluation) -> List[dict]:
    """Classify every install item. Returns plan findings (conflicts, refusals)."""
    lock, receipt = ev.lock, ev.receipt
    same_commit = receipt is not None and receipt.commit == lock.commit
    problems: List[dict] = []
    for item in ev.items:
        rec = receipt.files.get(item.path) if receipt else None
        dest = item.dest
        if item.entry is None:
            continue
        target_mode = item.entry.mode
        if dest.kind == "unsafe":
            item.action, item.reason = "refuse", "unsafe destination: %s" % dest.problem
            problems.append(_plan_finding("PATH_UNSAFE", item, item.reason))
            continue
        if dest.kind == "absent":
            item.action, item.reason = "create", "not installed"
            continue
        identical = dest.sha256 == item.pinned_sha256 and dest.mode == target_mode
        managed: Dict[str, str] = {}  # a hash this tool may find, with the mode it left it in
        if rec is not None:
            managed[rec.sha256] = rec.mode
            if receipt.state == "installing" and rec.previous_sha256:
                managed[rec.previous_sha256] = rec.previous_mode
        if identical:
            recorded = (rec is not None and same_commit and receipt.state == "complete"
                        and rec.sha256 == item.pinned_sha256 and rec.mode == target_mode)
            item.action = "current" if recorded else "record"
            item.reason = ("matches the pin and the receipt" if recorded else
                           "bytes already equal the pinned blob; only the receipt is written")
        elif managed.get(dest.sha256) == dest.mode:
            item.action = "upgrade"
            item.reason = ("known-managed earlier version (receipt commit %s)" % receipt.commit[:12])
        elif rec is not None:
            item.action = "refuse"
            item.reason = "managed file edited since it was installed; its bytes and mode are kept"
            problems.append(_plan_finding("CONFLICT_MANAGED_MODIFIED", item, item.reason))
        else:
            item.action = "refuse"
            item.reason = ("an unmanaged file with the pinned content but another executable bit already exists here"
                           if dest.sha256 == item.pinned_sha256 else
                           "an unmanaged file with different content already exists here")
            problems.append(_plan_finding("CONFLICT_UNMANAGED", item, item.reason))
    return problems


def _plan_finding(code: str, item: Item, message: str) -> dict:
    return {"code": code, "severity": "error", "path": item.path,
            "message": "%s: %s" % (item.path, message),
            "hint": "nothing was written; reconcile this file yourself (this tool never overwrites "
            "unrecognised content)"}


def _final_receipt(ev: Evaluation, state: str, with_previous: bool) -> Receipt:
    files: Dict[str, ReceiptFile] = {}
    for item in ev.items:
        previous = previous_mode = None
        if with_previous and item.action in ("upgrade", "current", "record"):
            previous, previous_mode = item.dest.sha256, item.dest.mode
        files[item.path] = ReceiptFile(item.path, item.pinned_sha256, item.entry.oid,
                                       item.entry.mode, previous, previous_mode)
    return Receipt(state, ev.lock.repository, ev.lock.commit, files)


def run_materialize(opts: Options) -> dict:
    """Plan read-only first. Only a plan that is executable and has something to
    write takes the exclusive lock, and then re-reads everything under it: a
    refused, dry or no-op run never touches the checkout (not even a lock file)."""
    # a dry run never fetches: fetching writes the cache, and a dry run writes nothing
    ev = evaluate(opts, allow_fetch=opts.allow_network and opts.apply)
    doc, work = _prepare(ev)
    if not opts.apply or work is None:
        return doc
    try:
        # before the lock file can be created: this failure leaves the checkout untouched
        fsops.require_support()
    except fsops.Unsupported as exc:
        return _record_failure(doc, InstallFailure("UNSUPPORTED_PLATFORM", str(exc)))
    guard = _acquire_lock(opts)
    if guard is None:
        return _busy_document(opts)
    try:
        locked = evaluate(opts)  # network, if permitted, was already used above
        locked.network_used = ev.network_used
        doc, work = _prepare(locked)
        if work is None:
            return doc
        return _apply(locked, doc, *work)
    finally:
        guard.release()


def _busy_document(opts: Options) -> dict:
    doc = error_document(opts, "materialize", "", "")
    doc["error"] = None
    doc["summary"]["exit_code"] = 1
    doc["findings"] = [{"code": "INSTALL_LOCKED", "severity": "error", "path": None,
                        "message": "another materialize is running for this checkout",
                        "hint": "nothing was changed; wait for it to finish, then run this again"}]
    doc["result"] = {"dry_run": False, "applied": False, "written": [], "receipt_written": False,
                     "post_verification": None}
    return doc


def _prepare(ev: Evaluation):
    """The plan document, and (pending, final receipt, receipt_changes) when there is
    something to write; None as the second value when the plan is refused or empty."""
    opts = ev.opts
    result = {"dry_run": not opts.apply, "applied": False, "written": [], "receipt_written": False,
              "post_verification": None}
    blocked = [f for f in ev.findings if f["severity"] == "error"]
    plan_problems: List[dict] = []
    if not blocked:
        plan_problems = _plan(ev)
    doc = document(ev, "materialize", {"result": result})
    doc["findings"] = ev.findings + plan_problems
    errors = [f for f in doc["findings"] if f["severity"] == "error"]
    doc["summary"]["errors"] = len(errors)
    if errors:
        doc["summary"]["exit_code"] = 1
        return doc, None
    pending = [i for i in ev.items if i.action in ("create", "upgrade")]
    final = _final_receipt(ev, "complete", False)
    receipt_changes = ev.receipt_raw != serialize_receipt(final)
    if not pending and not receipt_changes:
        result["applied"] = opts.apply
        return doc, None
    doc["findings"] = doc["findings"] + _leftover_findings(ev, pending)
    return doc, (pending, final, receipt_changes)


def _display_path(root: str, path: str) -> str:
    relative = os.path.relpath(path, root)
    return path if relative == os.pardir or relative.startswith(os.pardir + os.sep) else relative


def _leftover_findings(ev: Evaluation, pending: List[Item]) -> List[dict]:
    """Files beside the places this run will write that look like temporary files of an
    earlier run. The tool cannot tell its own leftover from a user's file by name, so it
    never removes or reuses one; it only says where they are."""
    root = ev.opts.root
    places = {(os.path.dirname(os.path.join(root, i.path)), STAGE_PREFIX) for i in pending}
    receipt = ev.opts.receipt_path
    places.add((os.path.dirname(receipt), _receipt_tmp_prefix(receipt)))
    found = []
    for directory, prefix in places:
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        found.extend(os.path.join(directory, n) for n in names if n.startswith(prefix))
    return [{"code": "STAGING_FILE_PRESERVED", "severity": "warning", "path": _display_path(root, path),
             "message": "a file named like a temporary file of this tool already exists; this run did "
                        "not create it and left it untouched",
             "hint": "if it is a leftover of an interrupted installation, check it and remove it by hand; "
                     "the installation does not depend on it"} for path in sorted(found)]


def _apply(ev: Evaluation, doc: dict, pending: List[Item], final: Receipt,
           receipt_changes: bool) -> dict:
    result = doc["result"]
    try:
        written = _execute(ev, pending, final, receipt_changes)
    except InstallFailure as exc:
        return _record_failure(doc, exc)
    result["applied"] = True
    result["written"] = written
    result["receipt_written"] = True
    try:
        after = evaluate(ev.opts)
    except ToolFailure as exc:  # the files are installed: say so, and that they could not be verified
        result["post_verification"] = {"local_integrity": "unknown", "upstream_provenance": "unknown",
                                       "ok": False}
        doc["summary"].update(local_integrity="unknown", upstream_provenance="unknown",
                              errors=doc["summary"]["errors"] + 1, exit_code=1)
        doc["findings"].append({"code": "POST_INSTALL_VERIFICATION_FAILED", "severity": "error", "path": None,
                                "message": "the files were installed, but verifying them failed: %s: %s"
                                % (exc.code, exc.message), "hint": "run verify for details"})
        return doc
    local, provenance = properties(after)
    ok = local == "ok" and provenance == "ok" and not [
        f for f in after.findings + after.state_findings if f["severity"] == "error"]
    result["post_verification"] = {"local_integrity": local, "upstream_provenance": provenance,
                                   "ok": ok}
    doc["summary"]["local_integrity"], doc["summary"]["upstream_provenance"] = local, provenance
    if not ok:
        doc["findings"].append({"code": "POST_INSTALL_VERIFICATION_FAILED", "severity": "error",
                                "path": None, "message": "the installed files do not verify after "
                                "the installation", "hint": "run verify for details"})
        doc["summary"].update(errors=doc["summary"]["errors"] + 1, exit_code=1)
    return doc


def _record_failure(doc: dict, exc: "InstallFailure") -> dict:
    doc["findings"].append({"code": exc.code, "severity": "error", "path": None,
                            "message": exc.message, "hint": exc.hint})
    doc["summary"].update(errors=doc["summary"]["errors"] + 1, exit_code=1)
    doc["result"]["written"] = exc.written
    return doc


class InstallFailure(Exception):
    def __init__(self, code: str, message: str, hint: Optional[str] = None,
                 written: Optional[List[str]] = None):
        super().__init__(message)
        self.code, self.message, self.hint, self.written = code, message, hint, written or []


def _write_receipt(ev: Evaluation, receipt: Receipt, tree: "fsops.Tree") -> None:
    """Replace the receipt through the held descriptor of its directory. The temporary
    file is the run's own (created exclusively, removed only by identity)."""
    path = ev.opts.receipt_path
    name = os.path.basename(path)
    tmp = None
    try:
        directory = tree.dir_of(os.path.dirname(path))
        try:
            info = os.stat(name, dir_fd=directory.fd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise InstallFailure("RECEIPT_PATH_UNSAFE", "the receipt path is not a regular file")
        except FileNotFoundError:
            pass
        tmp = fsops.create_exclusive(directory, _receipt_tmp_stem(path), serialize_receipt(receipt), "100644")
        tree.revalidate(directory)
        fsops.replace_created(tmp, name)
    except OSError as exc:
        if tmp is not None:
            fsops.remove_created(tmp)  # created by this call, and only while it is still that file
        raise InstallFailure("RECEIPT_WRITE_FAILED", "cannot write the install receipt: %s"
                             % (exc.strerror or exc))
    finally:
        if tmp is not None:
            tmp.close()
    fsops.fsync_dir(directory)


def _receipt_tmp_prefix(receipt_path: str) -> str:
    return ".%s.tmp." % os.path.basename(receipt_path)


def _receipt_tmp_stem(receipt_path: str) -> str:
    return "%s%d" % (_receipt_tmp_prefix(receipt_path), os.getpid())


def _discard(staged: List["fsops.Created"], tree: "fsops.Tree") -> None:
    """Undo staging: remove the staged copies and the directories this run created, each
    through its held descriptor and only while it is still what the run made."""
    for created in staged:
        fsops.remove_created(created)
    tree.remove_created_dirs()


def _execute(ev: Evaluation, pending: List[Item], final: Receipt, receipt_changes: bool) -> List[str]:
    try:
        tree = fsops.Tree(ev.opts.root)
    except OSError as exc:
        raise InstallFailure("STAGING_FAILED", "cannot open the checkout safely: %s; nothing was written"
                             % (exc.strerror or exc))
    staged: List[fsops.Created] = []
    try:
        return _execute_in(ev, tree, pending, final, staged)
    finally:
        for created in staged:
            created.close()
        tree.close()


def _execute_in(ev: Evaluation, tree: "fsops.Tree", pending: List[Item], final: Receipt,
                staged: List["fsops.Created"]) -> List[str]:
    root = ev.opts.root
    if not pending:
        _write_receipt(ev, final, tree)
        return []
    homes: List[Tuple[Item, "fsops.Created"]] = []
    try:
        for number, item in enumerate(pending):
            now = inspect_dest(root, item.path)
            if now.kind == "unsafe" or (now.sha256, now.mode) != (item.dest.sha256, item.dest.mode):
                raise InstallFailure("DESTINATION_CHANGED", "%s changed while planning; nothing was written"
                                     % item.path, "run again")
            directory = tree.root
            for part in item.path.split("/")[:-1]:
                directory = tree.descend(directory, part, create=True)
            stem = "%s%d.%d" % (STAGE_PREFIX, os.getpid(), number)
            created = fsops.create_exclusive(directory, stem, item.data, item.entry.mode)
            staged.append(created)
            homes.append((item, created))
    except (OSError, InstallFailure) as exc:
        _discard(staged, tree)
        if isinstance(exc, InstallFailure):
            raise
        raise InstallFailure("STAGING_FAILED", "cannot stage the new files: %s; no destination was modified"
                             % (getattr(exc, "strerror", None) or exc))
    journal = _final_receipt(ev, "installing", True)
    try:
        _write_receipt(ev, journal, tree)
    except InstallFailure:
        _discard(staged, tree)
        raise
    written: List[str] = []
    try:
        for item, created in homes:
            tree.revalidate(created.dir)
            now = inspect_in(created.dir, os.path.basename(item.path), item.path)
            if now.kind == "unsafe" or (now.sha256, now.mode) != (item.dest.sha256, item.dest.mode):
                raise OSError(0, "%s changed during the installation" % item.path)
            fsops.replace_created(created, os.path.basename(item.path))
            written.append(item.path)
        for _, created in homes:
            fsops.fsync_dir(created.dir)
    except OSError as exc:
        _discard(staged, tree)  # skips the copies already renamed into place
        raise InstallFailure(
            "INSTALL_INTERRUPTED",
            "installing failed after %d of %d file(s): %s" % (len(written), len(homes),
                                                              getattr(exc, "strerror", None) or exc),
            "recovery required: the receipt is now a journal. Fix the cause, then run: "
            + _command_hint(ev.opts, "materialize", "--apply"), written=written)
    try:
        _write_receipt(ev, final, tree)
    except InstallFailure as exc:
        raise InstallFailure(
            exc.code, "%d file(s) were installed but the final receipt could not be written: %s"
            % (len(written), exc.message),
            "recovery required: the receipt is still a journal. Fix the cause, then run: "
            + _command_hint(ev.opts, "materialize", "--apply"), written=written)
    return written


# -- exclusive access ----------------------------------------------------------------------

class _LockGuard:
    def __init__(self, fd: int):
        self.fd = fd

    def release(self) -> None:
        try:
            os.close(self.fd)  # closing drops the flock, also if the process dies
        except OSError:
            pass


def _acquire_lock(opts: Options) -> Optional[_LockGuard]:
    try:
        import fcntl
    except ImportError:
        raise ToolFailure("UNSUPPORTED_PLATFORM", "materialize --apply needs POSIX file locking")
    try:
        git_dir = run_git(["rev-parse", "--absolute-git-dir"], cwd=opts.root).stdout
    except GitFailure as exc:
        raise ToolFailure("NOT_A_REPOSITORY", "%s is not a Git checkout: %s" % (opts.root, exc))
    path = os.path.join(git_dir.decode("utf-8", "surrogateescape").strip(), "tooltempest-install.lock")
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        raise ToolFailure("IO_ERROR", "cannot open the install lock %s: %s" % (path, exc.strerror))
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    except OSError as exc:
        os.close(fd)
        raise ToolFailure("IO_ERROR", "cannot lock %s: %s" % (path, exc.strerror))
    return _LockGuard(fd)
