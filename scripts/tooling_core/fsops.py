"""Descriptor-anchored file operations for the installer.

Every create, remove and rename the installer makes goes through an open directory
descriptor, never through a path string that the kernel would resolve again at the
moment of the call. A directory is opened component by component, each relative to a
descriptor that is already held, with O_NOFOLLOW, and the descriptors are kept until the
transaction ends. A symlink put in place of any component after that point cannot
redirect an operation: the held descriptor still names the directory that was opened.

Ownership of a file is not a matter of its name. A file counts as created by this
transaction only because `create_exclusive` made it (O_CREAT|O_EXCL) and recorded its
device and inode; `remove_created` unlinks the name only while it still has that identity.
A directory counts as created by this transaction only because `Tree.descend` made it.
The descriptor of every file and directory the transaction made stays open until it ends,
so its inode cannot be freed and handed to another file: a matching device and inode
number can only mean the very object that was created.
Where a safe operation is impossible the functions raise (`UnsafePath`, `Unsupported`);
nothing falls back to a path-based delete.
"""
from __future__ import annotations

import errno
import os
from typing import List, Optional, Tuple

_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


class Unsupported(Exception):
    """The platform lacks what descriptor-anchored operations need."""


class UnsafePath(OSError):
    """A directory on the way is not the directory that was opened (a symlink, a swap)."""

    def __init__(self, message: str):
        super().__init__(errno.ELOOP, message)


def require_support() -> None:
    missing = []
    for func in (os.open, os.mkdir, os.unlink, os.rmdir, os.stat, os.rename):
        if func not in os.supports_dir_fd:
            missing.append(func.__name__)
    if os.stat not in os.supports_follow_symlinks:
        missing.append("stat(follow_symlinks)")
    for name in ("O_DIRECTORY", "O_NOFOLLOW"):
        if not hasattr(os, name):
            missing.append(name)
    if not hasattr(os, "fchmod"):
        missing.append("fchmod")
    if missing:
        raise Unsupported("this platform lacks %s, which the installer needs to write safely; "
                          "nothing was written" % ", ".join(missing))


class Dir:
    """An open directory with the identity it had when it was opened."""

    def __init__(self, fd: int, parent: Optional["Dir"], name: str, abs_path: Optional[str]):
        info = os.fstat(fd)
        self.fd, self.parent, self.name, self.abs_path = fd, parent, name, abs_path
        self.ident = (info.st_dev, info.st_ino)

    def chain(self) -> List["Dir"]:
        out, cur = [], self
        while cur is not None:
            out.append(cur)
            cur = cur.parent
        return list(reversed(out))

    def label(self) -> str:
        return "/".join(d.name for d in self.chain()[1:]) or (self.abs_path or ".")


def _open_dir(name: str, parent_fd: Optional[int]) -> int:
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise UnsafePath("%s is a symlink or not a directory" % name) from None
        raise


def _open_abs(path: str) -> int:
    """Open an absolute directory component by component without following symlinks.
    `path` must already be free of symlinks (a realpath); a component that has since
    become one fails instead of being followed."""
    fd = os.open("/", _DIR_FLAGS)
    try:
        for part in [p for p in path.split("/") if p]:
            nxt = _open_dir(part, fd)
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


class Tree:
    """The directories one installation works in; closes them all at the end."""

    def __init__(self, root: str):
        self.real = os.path.realpath(root)
        self._dirs: List[Dir] = []
        self.created: List[Dir] = []
        self.root = self._track(Dir(_open_abs(self.real), None, "", self.real))

    def _track(self, directory: Dir) -> Dir:
        self._dirs.append(directory)
        return directory

    def close(self) -> None:
        for directory in reversed(self._dirs):
            try:
                os.close(directory.fd)
            except OSError:
                pass
        self._dirs = []

    def descend(self, parent: Dir, name: str, create: bool = False) -> Dir:
        """The subdirectory `name` of `parent`, opened without following a symlink; made
        (and remembered as ours) when `create` and it does not exist."""
        made = False
        try:
            fd = _open_dir(name, parent.fd)
        except FileNotFoundError:
            if not create:
                raise
            os.mkdir(name, 0o777, dir_fd=parent.fd)
            made = True
            fd = _open_dir(name, parent.fd)
        directory = self._track(Dir(fd, parent, name, None))
        if made:
            self.created.append(directory)
        return directory

    def dir_of(self, path: str) -> Dir:
        """The directory at an absolute `path`: below the root when it is, otherwise
        opened on its own. Existing directories only."""
        real = os.path.realpath(path)
        if real == self.real:
            return self.root
        if real.startswith(self.real + os.sep):
            directory = self.root
            for part in real[len(self.real) + 1:].split(os.sep):
                directory = self.descend(directory, part)
            return directory
        return self._track(Dir(_open_abs(real), None, "", real))

    def revalidate(self, directory: Dir) -> None:
        """Raise UnsafePath unless the path that leads to `directory` still resolves, step
        by step and without symlinks, to the directories this transaction holds."""
        chain = directory.chain()
        top = chain[0]
        try:
            fd = _open_abs(top.abs_path)
        except FileNotFoundError:
            raise UnsafePath("%s is gone" % top.abs_path) from None
        try:
            if _ident(fd) != top.ident:
                raise UnsafePath("%s is no longer the directory that was opened" % top.abs_path)
            for held in chain[1:]:
                try:
                    nxt = _open_dir(held.name, fd)
                except FileNotFoundError:
                    raise UnsafePath("%s is gone" % held.name) from None
                os.close(fd)
                fd = nxt
                if _ident(fd) != held.ident:
                    raise UnsafePath("%s is no longer the directory that was opened" % held.name)
        finally:
            os.close(fd)

    def remove_created_dirs(self) -> None:
        """rmdir the directories this transaction made, innermost first, each only while its
        name in the held parent still has the identity it was made with, and only if empty."""
        for directory in reversed(self.created):
            try:
                info = os.stat(directory.name, dir_fd=directory.parent.fd, follow_symlinks=False)
                if (info.st_dev, info.st_ino) == directory.ident:
                    os.rmdir(directory.name, dir_fd=directory.parent.fd)
            except OSError:
                pass
        self.created = []


def _ident(fd: int) -> Tuple[int, int]:
    info = os.fstat(fd)
    return (info.st_dev, info.st_ino)


class Created:
    """A file this transaction created: where, which inode, and the open descriptor that
    keeps that inode from being reused until `close`."""

    def __init__(self, directory: Dir, name: str, fd: int):
        self.dir, self.name, self.fd, self.ident = directory, name, fd, _ident(fd)

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None


def _write(directory: Dir, name: str, data: bytes, mode: str) -> Created:
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=directory.fd)  # FileExistsError if taken
    created = Created(directory, name, fd)
    try:
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(fd)
            os.fchmod(fd, 0o755 if mode == "100755" else 0o644)
    except BaseException:
        remove_created(created)
        created.close()
        raise
    return created


def create_exclusive(directory: Dir, stem: str, data: bytes, mode: str, attempts: int = 64) -> Created:
    """Create a new file `stem` in `directory`, or `stem.<k>` when that name is taken. A
    taken name is never judged: whatever is there stays exactly as it is."""
    for attempt in range(attempts):
        try:
            return _write(directory, stem if attempt == 0 else "%s.%d" % (stem, attempt), data, mode)
        except FileExistsError:
            continue
    raise FileExistsError(errno.EEXIST, "no free temporary name for %s in %s" % (stem, directory.label()))


def remove_created(created: Created) -> bool:
    """Unlink a file this transaction created, relative to its held directory, and only
    while the name still refers to the inode that was created (a copy already renamed
    away leaves no such name). True if it was removed."""
    try:
        info = os.stat(created.name, dir_fd=created.dir.fd, follow_symlinks=False)
        if (info.st_dev, info.st_ino) != created.ident:
            return False
        os.unlink(created.name, dir_fd=created.dir.fd)
        return True
    except OSError:
        return False


def replace_created(created: Created, destination_name: str) -> None:
    """Rename a created file over `destination_name` in the same held directory, after
    checking that the name still refers to the inode that was created."""
    info = os.stat(created.name, dir_fd=created.dir.fd, follow_symlinks=False)
    if (info.st_dev, info.st_ino) != created.ident:
        raise UnsafePath("the staged copy %s is no longer the file that was created" % created.name)
    os.replace(created.name, destination_name, src_dir_fd=created.dir.fd, dst_dir_fd=created.dir.fd)


def fsync_dir(directory: Dir) -> None:
    try:
        os.fsync(directory.fd)
    except OSError:
        pass
