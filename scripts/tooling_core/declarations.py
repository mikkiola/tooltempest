"""The three small formats the tooling reads and writes, parsed strictly.

* the lock (`.tooltempest.lock`, owned by the consumer): which commit is
  pinned, and optionally which sha256 the pin declares for installed files;
* the manifest (`MANIFEST.txt` at the pinned commit): which paths exist;
* the install receipt (written only by `materialize --apply`): what was
  installed from which commit.

Every parser rejects what it does not understand (unknown keys, duplicate
JSON keys, wrong types, malformed paths) with a FormatError; nothing is
guessed or defaulted. Unicode-normalisation and case-insensitive collisions
(a case-insensitive file system maps two names to one file) count as
duplicates.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from . import RECEIPT_SCHEMA_VERSION, TOOL_NAME, TOOL_VERSION
from .objects import is_oid

MAX_TEXT_BYTES = 1024 * 1024
INSTALL_ROOTS = ("scripts", "schemas")  # repository-local; copied to the same relative path
GLOBAL_ROOTS = ("skills", "rules")  # client configuration under ~/.claude: never touched here
REFERENCE_PREFIX = "docs/reference/"  # consulted in place, never copied (ADR-0008)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}\Z")
MODES = ("100644", "100755")


class FormatError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _no_duplicates(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError("duplicate JSON key %r" % key)
        seen[key] = value
    return seen


def load_json_object(raw: bytes, code: str, what: str) -> dict:
    if len(raw) > MAX_TEXT_BYTES:
        raise FormatError(code, "%s is larger than %d bytes" % (what, MAX_TEXT_BYTES))
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (UnicodeDecodeError, ValueError) as exc:
        raise FormatError(code, "%s is not valid JSON: %s" % (what, exc))
    if not isinstance(data, dict):
        raise FormatError(code, "%s must be a JSON object" % what)
    return data


def path_problem(rel: object) -> Optional[str]:
    """Why `rel` cannot be a repository-relative path, or None."""
    if not isinstance(rel, str) or not rel:
        return "empty path"
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in rel):
        return "control character in path"
    if "\\" in rel:
        return "backslash in path"
    if rel.startswith("/"):
        return "absolute path"
    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return "empty, '.' or '..' path component"
    if any(part.casefold() == ".git" for part in parts):
        return "path inside a .git directory"
    return None


def collision_key(rel: str) -> str:
    return "/".join(unicodedata.normalize("NFC", part).casefold() for part in rel.split("/"))


def overlap_problem(paths: List[str]) -> Optional[str]:
    """A duplicate destination, or one that is a directory prefix of another."""
    keys: Dict[str, str] = {}
    for rel in paths:
        key = collision_key(rel)
        if key in keys:
            if keys[key] == rel:
                return "duplicate destination %s" % rel
            return "destinations %s and %s collide (case or Unicode normalisation)" % (keys[key], rel)
        keys[key] = rel
    ordered = sorted(keys)
    for first, second in zip(ordered, ordered[1:]):
        if second.startswith(first + "/"):
            return "destination %s is inside the file destination %s" % (keys[second], keys[first])
    return None


# -- lock ------------------------------------------------------------------------

@dataclass(frozen=True)
class Lock:
    repository: str
    commit: str
    declared: Dict[str, str]  # destination path -> sha256 the pin declares


_LOCK_KEYS = {"repository", "commit", "path", "sha256", "files"}


def parse_lock(raw: bytes) -> Lock:
    data = load_json_object(raw, "LOCK_MALFORMED", "the lock")
    unknown = sorted(set(data) - _LOCK_KEYS)
    if unknown:
        raise FormatError("LOCK_MALFORMED", "the lock has unknown field(s): %s" % ", ".join(unknown))
    repository = data.get("repository")
    if not isinstance(repository, str) or not repository:
        raise FormatError("LOCK_MALFORMED", "the lock needs a non-empty string 'repository'")
    commit = data.get("commit")
    if not is_oid(commit):
        raise FormatError("LOCK_MALFORMED",
                          "the lock's 'commit' must be a full 40- or 64-character lowercase hex "
                          "object id (never a branch, tag or abbreviation)")
    declared: Dict[str, str] = {}

    def declare(path: object, digest: object, where: str) -> None:
        problem = path_problem(path)
        if problem:
            raise FormatError("LOCK_MALFORMED", "%s: %s" % (where, problem))
        if not isinstance(digest, str) or not _SHA256_RE.match(digest):
            raise FormatError("LOCK_MALFORMED", "%s: 'sha256' must be 64 lowercase hex characters" % where)
        if path in declared:
            raise FormatError("LOCK_MALFORMED", "%s: %s is declared twice" % (where, path))
        declared[path] = digest

    if ("path" in data) != ("sha256" in data):
        raise FormatError("LOCK_MALFORMED", "'path' and 'sha256' must be given together")
    if "path" in data:
        declare(data["path"], data["sha256"], "the lock's path/sha256")
    files = data.get("files", [])
    if not isinstance(files, list):
        raise FormatError("LOCK_MALFORMED", "'files' must be a list")
    for number, item in enumerate(files):
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise FormatError("LOCK_MALFORMED", "files[%d] must be {path, sha256}" % number)
        declare(item["path"], item["sha256"], "files[%d]" % number)
    problem = overlap_problem(list(declared))
    if problem:
        raise FormatError("LOCK_MALFORMED", problem)
    return Lock(repository, commit, declared)


# -- manifest --------------------------------------------------------------------

@dataclass(frozen=True)
class Manifest:
    install: List[str]  # copied into the consumer at the same relative path
    skipped: List[Tuple[str, str]]  # (path, reason): never copied by this tool


def parse_manifest(raw: bytes) -> Manifest:
    if len(raw) > MAX_TEXT_BYTES:
        raise FormatError("MANIFEST_MALFORMED", "the manifest is larger than %d bytes" % MAX_TEXT_BYTES)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise FormatError("MANIFEST_MALFORMED", "the manifest is not valid UTF-8")
    install: List[str] = []
    skipped: List[Tuple[str, str]] = []
    seen: List[str] = []
    for number, line in enumerate(text.split("\n"), 1):
        if not line:
            continue
        problem = path_problem(line)
        if problem:
            raise FormatError("MANIFEST_MALFORMED", "line %d: %s" % (number, problem))
        seen.append(line)
        top = line.split("/", 1)[0]
        if line.startswith(REFERENCE_PREFIX):
            skipped.append((line, "reference document: consulted in place, not copied"))
        elif top in GLOBAL_ROOTS and "/" in line:
            skipped.append((line, "client configuration under ~/.claude: out of scope, never touched"))
        elif top in INSTALL_ROOTS and "/" in line:
            install.append(line)
        else:
            raise FormatError("MANIFEST_MALFORMED",
                              "line %d: no destination mapping for %s" % (number, line))
    if not seen:
        raise FormatError("MANIFEST_MALFORMED", "the manifest lists no files")
    problem = overlap_problem(seen)
    if problem:
        raise FormatError("MANIFEST_MALFORMED", problem)
    return Manifest(install, skipped)


# -- receipt ---------------------------------------------------------------------

@dataclass(frozen=True)
class ReceiptFile:
    path: str
    sha256: str
    git_blob: str
    mode: str
    previous_sha256: Optional[str] = None  # only while an installation is unfinished
    previous_mode: Optional[str] = None  # likewise: the mode that previous version had


@dataclass(frozen=True)
class Receipt:
    state: str  # "complete" | "installing"
    repository: str
    commit: str
    files: Dict[str, ReceiptFile]


_RECEIPT_KEYS = {"schema_version", "tool", "tool_version", "state", "repository", "commit", "files"}
_RECEIPT_FILE_KEYS = {"path", "sha256", "git_blob", "mode"}


def parse_receipt(raw: bytes) -> Receipt:
    data = load_json_object(raw, "RECEIPT_MALFORMED", "the install receipt")
    if set(data) != _RECEIPT_KEYS:
        raise FormatError("RECEIPT_MALFORMED", "the install receipt has missing or unknown fields")
    if data["schema_version"] != RECEIPT_SCHEMA_VERSION or isinstance(data["schema_version"], bool):
        raise FormatError("RECEIPT_MALFORMED", "unsupported receipt schema_version")
    if data["tool"] != TOOL_NAME or not isinstance(data["tool_version"], str) or not data["tool_version"]:
        raise FormatError("RECEIPT_MALFORMED", "the receipt was not written by this tool")
    if data["state"] not in ("complete", "installing"):
        raise FormatError("RECEIPT_MALFORMED", "receipt state must be 'complete' or 'installing'")
    if not isinstance(data["repository"], str) or not is_oid(data["commit"]):
        raise FormatError("RECEIPT_MALFORMED", "receipt repository/commit are malformed")
    if not isinstance(data["files"], list):
        raise FormatError("RECEIPT_MALFORMED", "receipt 'files' must be a list")
    installing = data["state"] == "installing"
    files: Dict[str, ReceiptFile] = {}
    for number, item in enumerate(data["files"]):
        where = "receipt files[%d]" % number
        wanted = _RECEIPT_FILE_KEYS | ({"previous_sha256", "previous_mode"} if installing else set())
        if not isinstance(item, dict) or set(item) != wanted:
            raise FormatError("RECEIPT_MALFORMED", "%s has missing or unknown fields" % where)
        problem = path_problem(item["path"])
        if problem or item["path"].split("/", 1)[0] not in INSTALL_ROOTS:
            raise FormatError("RECEIPT_MALFORMED", "%s: %s" % (where, problem or "not an installable path"))
        if not isinstance(item["sha256"], str) or not _SHA256_RE.match(item["sha256"]):
            raise FormatError("RECEIPT_MALFORMED", "%s: bad sha256" % where)
        if not is_oid(item["git_blob"]) or item["mode"] not in MODES:
            raise FormatError("RECEIPT_MALFORMED", "%s: bad git_blob or mode" % where)
        previous = item.get("previous_sha256")
        previous_mode = item.get("previous_mode")
        if installing and previous is not None and not (isinstance(previous, str)
                                                        and _SHA256_RE.match(previous)):
            raise FormatError("RECEIPT_MALFORMED", "%s: bad previous_sha256" % where)
        if installing and previous_mode is not None and previous_mode not in MODES:
            raise FormatError("RECEIPT_MALFORMED", "%s: bad previous_mode" % where)
        if installing and (previous is None) != (previous_mode is None):
            raise FormatError("RECEIPT_MALFORMED",
                              "%s: previous_sha256 and previous_mode must both be set or both be null" % where)
        if item["path"] in files:
            raise FormatError("RECEIPT_MALFORMED", "%s: %s listed twice" % (where, item["path"]))
        files[item["path"]] = ReceiptFile(item["path"], item["sha256"], item["git_blob"],
                                          item["mode"], previous, previous_mode)
    problem = overlap_problem(list(files))
    if problem:
        raise FormatError("RECEIPT_MALFORMED", problem)
    return Receipt(data["state"], data["repository"], data["commit"], files)


def serialize_receipt(receipt: Receipt) -> bytes:
    files = []
    for path in sorted(receipt.files):
        entry = receipt.files[path]
        item = {"path": entry.path, "sha256": entry.sha256, "git_blob": entry.git_blob,
                "mode": entry.mode}
        if receipt.state == "installing":
            item["previous_sha256"] = entry.previous_sha256
            item["previous_mode"] = entry.previous_mode
        files.append(item)
    document = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "state": receipt.state,
        "repository": receipt.repository,
        "commit": receipt.commit,
        "files": files,
    }
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
