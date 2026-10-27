"""Read-only access to a Git snapshot: the index or a commit's tree.

Every function here only reads Git objects or the index, never the
working tree, and never writes: no staging, no refs, no config, no
`git write-tree` (which would add objects), no network (lazy fetch is
disabled), and `GIT_OPTIONAL_LOCKS=0` so Git does not opportunistically
refresh the index. A Git failure raises GitError; it is never turned
into an empty snapshot.

The caller's GIT_* environment is inherited on purpose (a pre-commit hook
relies on GIT_INDEX_FILE to name the index being committed), so GIT_DIR
and friends, if set, decide which repository is read.

The snapshot is listed once; file content is then read by blob OID, so
a concurrent change to the index or a ref cannot alter what one run
inspects.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional

GIT_TIMEOUT_SEC = 60.0

_OID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_ZERO_OID_RE = re.compile(r"^0{40}(?:0{24})?\Z")

MODE_REGULAR = ("100644", "100755")


class GitError(Exception):
    """Git could not be run or reported an error. Never means 'no SPEC'."""


@dataclass(frozen=True)
class Entry:
    mode: str
    oid: str
    stage: int
    path: str


def is_oid(value: str) -> bool:
    return bool(_OID_RE.match(value))


def is_zero_oid(value: str) -> bool:
    return bool(_ZERO_OID_RE.match(value))


def _env() -> Dict[str, str]:
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_NO_LAZY_FETCH"] = "1"
    env["GIT_NO_REPLACE_OBJECTS"] = "1"  # judge the real object, never a refs/replace substitute
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["LC_ALL"] = "C"
    return env


def _run(cwd: str, args: List[str], input_bytes: Optional[bytes] = None) -> bytes:
    command = ["git", "-C", cwd] + args
    stdin = {"input": input_bytes} if input_bytes is not None else {"stdin": subprocess.DEVNULL}
    try:
        proc = subprocess.run(
            command,
            **stdin,
            capture_output=True,
            env=_env(),
            timeout=GIT_TIMEOUT_SEC,
            check=False,
        )
    except FileNotFoundError:
        raise GitError("git executable not found")
    except subprocess.TimeoutExpired:
        raise GitError("git %s timed out after %ss" % (args[0], GIT_TIMEOUT_SEC))
    except OSError as exc:
        raise GitError("cannot run git: %s" % exc)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise GitError(
            "git %s failed (exit %d): %s"
            % (args[0], proc.returncode, detail[0] if detail else "no diagnostic")
        )
    return proc.stdout


@dataclass
class Repository:
    cwd: str
    bare: bool

    def read_blob(self, oid: str) -> bytes:
        if not _OID_RE.match(oid):
            raise GitError("refusing to read non-OID object name %r" % oid)
        return _run(self.cwd, ["cat-file", "blob", oid])


def open_repository(path: str) -> Repository:
    """Locate the repository containing `path` (must exist and be a Git repo)."""
    if not os.path.isdir(path):
        raise GitError("repository path %r is not a directory" % path)
    bare = _run(path, ["rev-parse", "--is-bare-repository"]).decode().strip()
    if bare == "true":
        return Repository(path, True)
    top = _run(path, ["rev-parse", "--show-toplevel"]).decode("utf-8", "surrogateescape")
    return Repository(top.rstrip("\n"), False)


@dataclass
class Snapshot:
    kind: str  # "index" | "commit"
    identity: Dict[str, Optional[str]]
    entries: List[Entry]
    repo: Repository

    def read_blob(self, oid: str) -> bytes:
        return self.repo.read_blob(oid)


def _decode_path(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")


def _parse_ls_files(output: bytes) -> List[Entry]:
    entries = []
    for record in output.split(b"\0"):
        if not record:
            continue
        meta, sep, path = record.partition(b"\t")
        fields = meta.split(b" ")
        if not sep or len(fields) != 3:
            raise GitError("unparseable ls-files record")
        mode, oid, stage = fields[0].decode(), fields[1].decode(), fields[2].decode()
        entries.append(Entry(mode, oid, int(stage), _decode_path(path)))
    return entries


def _parse_ls_tree(output: bytes) -> List[Entry]:
    entries = []
    for record in output.split(b"\0"):
        if not record:
            continue
        meta, sep, path = record.partition(b"\t")
        fields = meta.split(b" ")
        if not sep or len(fields) != 3:
            raise GitError("unparseable ls-tree record")
        entries.append(Entry(fields[0].decode(), fields[2].decode(), 0, _decode_path(path)))
    return entries


def index_snapshot(repo: Repository) -> Snapshot:
    if repo.bare:
        raise GitError("a bare repository has no index to inspect")
    entries = _parse_ls_files(_run(repo.cwd, ["ls-files", "--stage", "-z"]))
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda e: (e.path, e.stage)):
        digest.update(
            ("%s %s %d\t" % (entry.mode, entry.oid, entry.stage)).encode()
            + entry.path.encode("utf-8", "surrogateescape")
            + b"\0"
        )
    return Snapshot(
        "index",
        {"commit": None, "tree": None, "index_digest": digest.hexdigest()},
        entries,
        repo,
    )


def resolve_commit(repo: Repository, rev: str) -> str:
    """Resolve a ref name, abbreviated or full OID, to a full commit OID."""
    if not rev or "\0" in rev or "\n" in rev:
        raise GitError("invalid revision %r" % rev)
    oid = _run(
        repo.cwd, ["rev-parse", "--verify", "--quiet", "--end-of-options", rev + "^{commit}"]
    ).decode().strip()
    if not _OID_RE.match(oid):
        raise GitError("revision %r did not resolve to a commit" % rev)
    return oid


def commit_snapshot(repo: Repository, rev: str) -> Snapshot:
    try:
        commit = resolve_commit(repo, rev)
    except GitError as exc:
        # `rev-parse --verify --quiet` exits 1 with no message for unknown names.
        if "no diagnostic" in str(exc):
            raise GitError("revision %r does not resolve to a commit" % rev)
        raise
    tree = _run(repo.cwd, ["rev-parse", "--verify", "--end-of-options", commit + "^{tree}"]).decode().strip()
    entries = _parse_ls_tree(_run(repo.cwd, ["ls-tree", "-r", "-z", "--full-tree", commit]))
    return Snapshot("commit", {"commit": commit, "tree": tree, "index_digest": None}, entries, repo)


def object_type(repo: Repository, oid: str) -> Optional[str]:
    """The Git object type named by a full OID ("tree", "commit", "blob", "tag"),
    or None when the object is absent from this repository (a name that is only an abbreviation of a
    different object, for example a 40-digit id in a sha256 repository, counts as
    absent). Other Git failures raise GitError: an absent object is never
    confused with a broken Git."""
    if not _OID_RE.match(oid):
        raise GitError("refusing to look up non-OID object name %r" % oid)
    out = _run(repo.cwd, ["cat-file", "--batch-check"], (oid + "\n").encode()).decode().split()
    if len(out) == 3 and out[0] == oid:
        return out[1]
    if len(out) == 3 or (len(out) == 2 and out[1] in ("missing", "ambiguous")):
        return None  # absent, or only an abbreviation of some other object: not this OID
    raise GitError("unparseable cat-file --batch-check answer %r" % " ".join(out))


def commit_tree_oid(repo: Repository, oid: str) -> Optional[str]:
    """The tree OID of a commit named by a full OID, or None when that object
    is absent or is not a commit."""
    if object_type(repo, oid) != "commit":
        return None
    return _run(repo.cwd, ["rev-parse", "--verify", "--end-of-options", oid + "^{tree}"]).decode().strip()


def list_tree(repo: Repository, tree_oid: str) -> List[Entry]:
    """Every non-tree entry beneath a tree object, by path. The caller has
    established with object_type() that `tree_oid` is a tree."""
    if object_type(repo, tree_oid) != "tree":
        raise GitError("%s is not a tree object in this repository" % tree_oid)
    return _parse_ls_tree(_run(repo.cwd, ["ls-tree", "-r", "-z", "--full-tree", tree_oid]))


def index_tree_oid(entries: List[Entry], hex_length: int) -> Optional[str]:
    """The OID `git write-tree` would give the index, computed in memory so that
    no object is written. None when the index holds unmerged entries (it has
    no tree). `hex_length` (40 or 64) picks the hash only for an empty index."""
    if any(entry.stage != 0 for entry in entries):
        return None
    if entries:
        hex_length = len(entries[0].oid)
    hasher = hashlib.sha1 if hex_length == 40 else hashlib.sha256
    root: dict = {}
    for entry in entries:
        parts = entry.path.encode("utf-8", "surrogateescape").split(b"/")
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = (entry.mode, entry.oid)

    def build(node: dict) -> str:
        items = []
        for name, value in node.items():
            if isinstance(value, dict):
                items.append((name + b"/", b"40000", name, build(value)))
            else:
                items.append((name, value[0].encode(), name, value[1]))
        items.sort(key=lambda item: item[0])  # Git orders a directory as if named "name/"
        body = b"".join(mode + b" " + name + b"\0" + bytes.fromhex(oid)
                        for _key, mode, name, oid in items)
        return hasher(b"tree %d\0" % len(body) + body).hexdigest()

    return build(root)
