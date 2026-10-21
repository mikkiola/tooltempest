"""Repository SPEC registry ("policy"): which SPEC files are tracks.

The policy is read from the inspected snapshot itself (never from the
working tree), so a staged or pushed policy governs exactly the content
it ships with. It registers *where* SPECs are; whether a SPEC is active
or idle is declared by the SPEC (see specparse), not here, so there is
no second copy of that fact to go stale when a new SPEC overwrites an
idle one.

Without a policy file the registry is implicit: a root-level SPEC.md,
if present, is the single track named "root". Every other SPEC.md in
the snapshot is out of scope and is reported as such by the checker.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List, Tuple

POLICY_PATH = ".docops-policy.json"
SUPPORTED_SCHEMA_VERSION = 1
DEFAULT_SPEC_PATH = "SPEC.md"
DEFAULT_TRACK = "root"

_TRACK_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class PolicyError(Exception):
    """The policy file is unreadable or invalid. Never means 'no policy'."""


@dataclass(frozen=True)
class TrackSpec:
    path: str
    track: str


@dataclass(frozen=True)
class Policy:
    source: str  # "default" | "snapshot"
    tracks: Tuple[TrackSpec, ...]

    @property
    def implicit(self) -> bool:
        return self.source == "default"


def default_policy() -> Policy:
    return Policy("default", (TrackSpec(DEFAULT_SPEC_PATH, DEFAULT_TRACK),))


def _reject_duplicate_keys(pairs):
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise PolicyError("duplicate key in JSON object")
    return dict(pairs)


def validate_spec_path(path: object) -> str:
    if not isinstance(path, str) or not path:
        raise PolicyError("spec path must be a non-empty string")
    if len(path) > 4096:
        raise PolicyError("spec path is too long")
    if path.startswith("/") or "\\" in path or _CONTROL_RE.search(path):
        raise PolicyError(
            "spec path %r must be a relative POSIX path without backslashes or control "
            "characters" % path
        )
    parts = path.split("/")
    for part in parts:
        if part in ("", ".", "..", ".git"):
            raise PolicyError("spec path %r has an invalid component %r" % (path, part))
    return path


def parse_policy(data: bytes) -> Policy:
    """Parse and strictly validate a policy file's bytes."""
    try:
        text = data.decode("utf-8")
        if text.startswith("﻿"):
            text = text[1:]
        document = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, ValueError, PolicyError) as exc:
        raise PolicyError("policy is not valid UTF-8 JSON: %s" % exc)

    if not isinstance(document, dict):
        raise PolicyError("policy must be a JSON object")
    unknown = sorted(set(document) - {"schema_version", "specs"})
    if unknown:
        raise PolicyError("unknown policy key(s): %s" % ", ".join(unknown))
    version = document.get("schema_version")
    if isinstance(version, bool) or version != SUPPORTED_SCHEMA_VERSION:
        raise PolicyError(
            "unsupported or missing schema_version %r (supported: %d)"
            % (version, SUPPORTED_SCHEMA_VERSION)
        )
    specs = document.get("specs")
    if not isinstance(specs, list):
        raise PolicyError("'specs' must be a list")

    tracks: List[TrackSpec] = []
    seen_paths = set()
    seen_names = set()
    for index, entry in enumerate(specs):
        if not isinstance(entry, dict):
            raise PolicyError("specs[%d] must be an object" % index)
        extra = sorted(set(entry) - {"path", "track"})
        if extra:
            raise PolicyError("specs[%d] has unknown key(s): %s" % (index, ", ".join(extra)))
        path = validate_spec_path(entry.get("path"))
        track = entry.get("track")
        if not isinstance(track, str) or not _TRACK_RE.fullmatch(track):
            raise PolicyError(
                "specs[%d].track must match %s" % (index, _TRACK_RE.pattern)
            )
        if path in seen_paths:
            raise PolicyError("duplicate spec path %r" % path)
        if track in seen_names:
            raise PolicyError("duplicate track name %r" % track)
        seen_paths.add(path)
        seen_names.add(track)
        tracks.append(TrackSpec(path, track))
    return Policy("snapshot", tuple(tracks))
