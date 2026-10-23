"""Verified Git-object access to the source of a pinned ToolTempest commit.

Bytes are never read from a working tree. A pinned commit is resolved by
following the object graph (commit, then each tree on the path, then the
blob) and every object read is re-hashed: its Git object id must equal the
hash of "<type> <size>\\0<content>". A mutable checkout, a dirty cache, a
moved HEAD or a tampered object stored under a wrong name therefore cannot
change what is installed or reported.

Reading never fetches: lazy fetch is disabled and no network protocol is
allowed. The only network function is `fetch_commit`, which callers invoke
only when the user explicitly permitted it.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

GIT_TIMEOUT_SEC = 60.0
FETCH_TIMEOUT_SEC = 300.0
MAX_BLOB_BYTES = 16 * 1024 * 1024

_OID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")

MODE_TREE = "40000"
MODE_FILES = ("100644", "100755")
MODE_SYMLINK = "120000"
MODE_GITLINK = "160000"


class ObjectError(Exception):
    """Base class; `code` is stable and shown to users."""

    code = "OBJECT_ERROR"


class GitFailure(ObjectError):
    """Git could not be run, or reported an error."""

    code = "GIT_FAILED"


class GitMissing(GitFailure):
    code = "GIT_MISSING"


class ObjectMissing(ObjectError):
    code = "OBJECT_MISSING"


class ObjectCorrupt(ObjectError):
    code = "OBJECT_CORRUPT"


def is_oid(value: object) -> bool:
    return isinstance(value, str) and bool(_OID_RE.match(value))


def git_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Environment for every Git call: no inherited GIT_* (a hook's GIT_DIR or
    GIT_INDEX_FILE must not redirect us), no prompts, no lazy fetch, no
    replace refs, no optional index refresh."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "none",
        "LC_ALL": "C",
    })
    if extra:
        env.update(extra)
    return env


def run_git(args: List[str], cwd: Optional[str] = None, git_dir: Optional[str] = None,
            input_bytes: Optional[bytes] = None, env: Optional[Dict[str, str]] = None,
            timeout: float = GIT_TIMEOUT_SEC, ok_codes: Tuple[int, ...] = (0,)
            ) -> subprocess.CompletedProcess:
    command = ["git"]
    if git_dir is not None:
        command += ["--git-dir", git_dir]
    if cwd is not None:
        command += ["-C", cwd]
    command += args
    kwargs = {"input": input_bytes} if input_bytes is not None else {"stdin": subprocess.DEVNULL}
    try:
        proc = subprocess.run(command, capture_output=True, env=env or git_env(),
                              timeout=timeout, check=False, **kwargs)
    except FileNotFoundError:
        raise GitMissing("git executable not found")
    except subprocess.TimeoutExpired:
        raise GitFailure("git %s timed out after %ss" % (args[0], timeout))
    except OSError as exc:
        raise GitFailure("cannot run git: %s" % exc)
    if proc.returncode not in ok_codes:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise GitFailure("git %s failed (exit %d): %s"
                         % (args[0], proc.returncode, detail[0] if detail else "no diagnostic"))
    return proc


@dataclass(frozen=True)
class TreeEntry:
    mode: str
    oid: str


def _hash_object(kind: str, content: bytes, oid_length: int) -> str:
    algo = hashlib.sha1 if oid_length == 40 else hashlib.sha256
    digest = algo(("%s %d\0" % (kind, len(content))).encode("ascii"))
    digest.update(content)
    return digest.hexdigest()


def parse_tree(data: bytes, oid_length: int) -> Dict[bytes, TreeEntry]:
    raw_length = oid_length // 2
    entries: Dict[bytes, TreeEntry] = {}
    index = 0
    while index < len(data):
        space = data.find(b" ", index)
        nul = data.find(b"\0", space + 1) if space >= 0 else -1
        if space < 0 or nul < 0 or nul + 1 + raw_length > len(data):
            raise ObjectCorrupt("malformed tree object")
        mode = data[index:space].decode("ascii", "replace")
        name = data[space + 1:nul]
        oid = data[nul + 1:nul + 1 + raw_length].hex()
        if not name or name in entries:
            raise ObjectCorrupt("malformed tree object (empty or repeated entry name)")
        entries[name] = TreeEntry(mode, oid)
        index = nul + 1 + raw_length
    return entries


class ObjectStore:
    """A Git object database addressed by its git directory. Read-only."""

    def __init__(self, path: str):
        self.path = path
        self.git_dir = self._locate(path)
        self._trees: Dict[str, Dict[bytes, TreeEntry]] = {}
        self._batch: Optional[subprocess.Popen] = None

    @staticmethod
    def _locate(path: str) -> str:
        if not os.path.isdir(path):
            raise GitFailure("source %r is not a directory" % path)
        git_dir = run_git(["rev-parse", "--absolute-git-dir"], cwd=path).stdout
        git_dir = git_dir.decode("utf-8", "surrogateescape").strip()
        # a plain directory inside some other repository must not silently borrow that repository
        here = os.path.realpath(path)
        if os.path.realpath(git_dir) != here:
            top = run_git(["rev-parse", "--show-toplevel"], cwd=path, ok_codes=(0, 128)).stdout
            if os.path.realpath(top.decode("utf-8", "surrogateescape").strip() or os.sep) != here:
                raise GitFailure("%r is not the root of a Git repository" % path)
        return git_dir

    # -- objects -------------------------------------------------------------
    def _batch_process(self) -> subprocess.Popen:
        if self._batch is None or self._batch.poll() is not None:
            try:
                self._batch = subprocess.Popen(
                    ["git", "--git-dir", self.git_dir, "cat-file", "--batch"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    env=git_env())
            except FileNotFoundError:
                raise GitMissing("git executable not found")
            except OSError as exc:
                raise GitFailure("cannot run git: %s" % exc)
        return self._batch

    def close(self) -> None:
        proc, self._batch = self._batch, None
        if proc is not None:
            try:
                proc.stdin.close()
                proc.stdout.close()
                proc.wait(timeout=GIT_TIMEOUT_SEC)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()

    def read_object(self, oid: str) -> Tuple[str, bytes]:
        """One object, re-hashed. Served by a single long-lived `cat-file --batch`
        process; a broken pipe or a short read is a Git failure, never data."""
        if not is_oid(oid):
            raise ObjectError("refusing to read non-OID object name %r" % (oid,))
        proc = self._batch_process()
        try:
            proc.stdin.write((oid + "\n").encode("ascii"))
            proc.stdin.flush()
            header = proc.stdout.readline()
            fields = header.decode("ascii", "replace").strip().split(" ")
            if len(fields) == 2 and fields[1] == "missing":
                raise ObjectMissing("object %s is not in the source" % oid)
            if len(fields) != 3 or fields[0] != oid or not fields[2].isdigit():
                self.close()  # the stream position is unknown now
                raise ObjectCorrupt("unparseable object header for %s" % oid)
            kind, size = fields[1], int(fields[2])
            if size > MAX_BLOB_BYTES:
                self.close()  # the unread content would desynchronise the next read
                raise ObjectCorrupt("object %s is larger than %d bytes" % (oid, MAX_BLOB_BYTES))
            content = proc.stdout.read(size)
            trailer = proc.stdout.read(1)
        except (OSError, ValueError) as exc:
            self.close()
            raise GitFailure("git cat-file failed while reading %s: %s" % (oid, exc))
        if len(content) != size or trailer != b"\n":
            self.close()
            raise GitFailure("git cat-file returned a truncated object for %s" % oid)
        if _hash_object(kind, content, len(oid)) != oid:
            raise ObjectCorrupt("object %s does not hash to its own name" % oid)
        return kind, content

    def read_typed(self, oid: str, kind: str) -> bytes:
        found, content = self.read_object(oid)
        if found != kind:
            raise ObjectCorrupt("object %s is a %s, expected a %s" % (oid, found, kind))
        return content

    def commit_tree(self, commit_oid: str) -> str:
        content = self.read_typed(commit_oid, "commit")
        first = content.split(b"\n", 1)[0].decode("ascii", "replace")
        tree = first[5:] if first.startswith("tree ") else ""
        if not is_oid(tree) or len(tree) != len(commit_oid):
            raise ObjectCorrupt("commit %s has no valid tree line" % commit_oid)
        return tree

    def _tree(self, oid: str) -> Dict[bytes, TreeEntry]:
        if oid not in self._trees:
            self._trees[oid] = parse_tree(self.read_typed(oid, "tree"), len(oid))
        return self._trees[oid]

    def resolve(self, commit_oid: str, path: str) -> Optional[TreeEntry]:
        """The entry at `path` in the commit's tree, or None if there is none.
        Every tree on the way is read and hash-verified."""
        current = self.commit_tree(commit_oid)
        parts = path.split("/")
        for position, part in enumerate(parts):
            entry = self._tree(current).get(part.encode("utf-8"))
            if entry is None:
                return None
            if position == len(parts) - 1:
                return entry
            if entry.mode != MODE_TREE:
                return None
            current = entry.oid
        return None

    def read_blob(self, oid: str) -> bytes:
        return self.read_typed(oid, "blob")

    # -- metadata (advisory, never a source of bytes) --------------------------
    def remote_url(self) -> Optional[str]:
        proc = run_git(["config", "--get", "remote.origin.url"], git_dir=self.git_dir,
                       ok_codes=(0, 1))
        value = proc.stdout.decode("utf-8", "replace").strip()
        return value or None

    def ref_commit(self, ref: str) -> Optional[str]:
        proc = run_git(["rev-parse", "--verify", "--quiet", ref + "^{commit}"],
                       git_dir=self.git_dir, ok_codes=(0, 1))
        value = proc.stdout.decode("ascii", "replace").strip()
        return value if is_oid(value) else None

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        proc = run_git(["merge-base", "--is-ancestor", ancestor, descendant],
                       git_dir=self.git_dir, ok_codes=(0, 1))
        return proc.returncode == 0

    def count_between(self, base: str, tip: str) -> int:
        proc = run_git(["rev-list", "--count", "%s..%s" % (base, tip)], git_dir=self.git_dir)
        return int(proc.stdout.decode("ascii").strip())


_FETCH_SCHEMES = ("https://", "file://")


def fetch_url_problem(url: object) -> Optional[str]:
    """Why a lock's repository value may not be fetched from, or None."""
    if not isinstance(url, str) or not url:
        return "the lock has no repository URL"
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in url) or url.startswith("-"):
        return "the repository URL contains control characters or starts with '-'"
    if url.startswith(_FETCH_SCHEMES) or os.path.isabs(url):
        return None
    return "the repository URL scheme is not allowed (https://, file:// or an absolute path)"


def fetch_commit(source: str, url: str, commit: str) -> None:
    """Fetch exactly one commit (and its history) into the source's object
    store, creating a bare store if the path does not exist. Called only on
    explicit permission. The commit is kept reachable under
    refs/tooltempest/pins/<commit>; no branch, tag or working tree is touched."""
    problem = fetch_url_problem(url)
    if problem:
        raise GitFailure(problem)
    if not is_oid(commit):
        raise GitFailure("refusing to fetch a non-OID commit name")
    if not os.path.isdir(source):
        os.makedirs(source, exist_ok=True)
        extra = ["--object-format=sha256"] if len(commit) == 64 else []
        run_git(["init", "--bare", "-q"] + extra + [source])
    git_dir = ObjectStore._locate(source)
    env = git_env({"GIT_ALLOW_PROTOCOL": "https:file"})
    run_git(["-c", "protocol.ext.allow=never", "fetch", "--quiet", "--no-tags", url,
             "+%s:refs/tooltempest/pins/%s" % (commit, commit)],
            git_dir=git_dir, env=env, timeout=FETCH_TIMEOUT_SEC)
