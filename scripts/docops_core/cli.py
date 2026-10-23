"""Command line surface for scripts/docops.py: `check` and `report`.

Both commands are read-only. `check` prints the versioned result as
JSON on stdout; `report` prints the same result as text. Exit codes
(also in the result's summary.exit_code):

  0  every evaluated check is PASS or N/A
  1  an invariant is violated (FAIL), or a supplied impact report's required
     assessment is not complete (its dimension is SKIP: missing, stale or
     unverifiable evidence)
  2  the checker could not produce a verdict: usage, dependency, I/O, Git,
     policy or internal error. This is a tool failure reported through the
     exit code and an `error` object, not a per-check status.
  3  SPEC applicability cannot be established (SKIP of SPEC_STRUCTURE)

`--impact-file PATH|-` additionally validates an externally written impact
report against each inspected snapshot (DOC_IMPACT_ASSESSMENT). A report that
cannot be read at all (missing file, I/O error) is a tool failure (exit 2); one
that is read but malformed or inconsistent is a FAIL of that dimension.

No failure path exits 0.
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from typing import List, Optional

from . import checks, gitstate, impact
from .gitstate import GitError

_PUSH_USAGE = "expected '<local ref> <local oid> <remote ref> <remote oid>' (git pre-push stdin)"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docops.py",
        description="Read-only DocOps checker. Inspects exactly the snapshot it is pointed at.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (
        ("check", "evaluate snapshots and print the versioned JSON result"),
        ("report", "evaluate snapshots and print a human-readable report"),
    ):
        cmd = sub.add_parser(name, help=text, description=text)
        cmd.add_argument(
            "--tree",
            action="append",
            default=[],
            metavar="index|REV",
            help="snapshot to inspect: 'index' (the staged tree) or a commit-ish "
            "(ref name or OID; the commit's own tree, never the working tree). "
            "Repeatable.",
        )
        cmd.add_argument(
            "--push-stdin",
            action="store_true",
            help="read git pre-push ref updates from stdin and inspect each pushed "
            "local OID; a deleted ref (all-zero OID) has nothing to check",
        )
        cmd.add_argument(
            "--impact-file",
            metavar="PATH|-",
            help="validate an impact report (schemas/docops-impact-report.schema.json) written "
            "by the reviewer, against each inspected snapshot; '-' reads standard input "
            "(not combinable with --push-stdin). Never written, generated or edited.",
        )
        cmd.add_argument("--repo", default=".", help="repository path (default: current directory)")
    return parser


def _parse_push_updates(raw: bytes) -> List[dict]:
    """Return one request per stdin line; malformed lines become error requests."""
    requests = []
    text = raw.decode("utf-8", "surrogateescape")
    # Split on LF only: ref names may legally contain other Unicode line separators.
    for number, line in enumerate(text.split("\n"), start=1):
        line = line.rstrip("\r")
        if not line.strip():
            continue
        fields = line.split(" ")
        if len(fields) != 4:
            requests.append({"kind": "invalid-update", "line": number, "text": line[:200],
                             "error": "line %d: %s" % (number, _PUSH_USAGE)})
            continue
        local_ref, local_oid, remote_ref, remote_oid = fields
        if not gitstate.is_oid(local_oid):
            requests.append({"kind": "invalid-update", "line": number, "text": line[:200],
                             "error": "line %d: local object name %r is not a full hexadecimal "
                             "OID" % (number, local_oid[:80])})
        elif gitstate.is_zero_oid(local_oid):
            requests.append({"kind": "deleted-ref", "local_ref": local_ref,
                             "local_oid": local_oid, "remote_ref": remote_ref,
                             "remote_oid": remote_oid})
        else:
            requests.append({"kind": "rev", "rev": local_oid, "local_ref": local_ref,
                             "local_oid": local_oid, "remote_ref": remote_ref,
                             "remote_oid": remote_oid})
    return requests


class ImpactReadError(Exception):
    """The impact report could not be read at all (a tool failure, not a FAIL)."""


def _read_impact(args: argparse.Namespace) -> Optional[impact.ParsedReport]:
    if args.impact_file is None:
        return None
    limit = impact.MAX_REPORT_BYTES + 1
    try:
        if args.impact_file == "-":
            raw, source, path = sys.stdin.buffer.read(limit), "stdin", None
        else:
            with open(args.impact_file, "rb") as handle:
                raw, source, path = handle.read(limit), "file", args.impact_file
    except (OSError, ValueError) as exc:
        raise ImpactReadError("impact report could not be read: %s" % exc)
    return impact.load_report(raw, source, path)


def _evaluate(args: argparse.Namespace, stdin_bytes: Optional[bytes],
              report: Optional[impact.ParsedReport] = None) -> dict:
    root: Optional[str] = None
    try:
        repo = gitstate.open_repository(args.repo)
        root = repo.cwd
    except GitError as exc:
        return checks.build_result(args.command, None, [],
                                   "repository could not be opened: %s" % exc)

    requests: List[dict] = []
    for tree in args.tree:
        requests.append({"kind": "index"} if tree == "index" else {"kind": "rev", "rev": tree})
    if args.push_stdin:
        updates = _parse_push_updates(stdin_bytes or b"")
        if not updates and not requests:
            return checks.build_result(args.command, root, [], "--push-stdin received no ref updates")
        requests.extend(updates)

    results = []
    for request in requests:
        if request["kind"] == "invalid-update":
            results.append(checks.error_snapshot_result(request, "INVALID_PUSH_UPDATE",
                                                        request["error"]))
        elif request["kind"] == "deleted-ref":
            results.append(checks.deleted_snapshot_result(request, report))
        else:
            results.append(checks.check_request(repo, request, report))
    return checks.build_result(args.command, root, results)


def _safe(value: object) -> str:
    """Make snapshot-derived text safe for a terminal: control, format (bidi, zero
    width), line/paragraph separator and lone-surrogate characters become escapes."""
    out = []
    for c in str(value):
        if c != "\t" and unicodedata.category(c) in ("Cc", "Cf", "Cs", "Zl", "Zp"):
            out.append("\\x%02x" % ord(c) if ord(c) < 256 else "\\u%04x" % ord(c))
        else:
            out.append(c)
    return "".join(out)


def render_text(result: dict) -> str:
    return "".join(_safe_line(line) for line in _render_lines(result).splitlines(True))


def _safe_line(line: str) -> str:
    body = line.rstrip("\n")
    return _safe(body) + line[len(body):]


def _render_lines(result: dict) -> str:
    out = ["docops %s (result schema v%d, tool %s)"
           % (result["command"], result["schema_version"], result["tool"]["version"])]
    out.append("repository: %s" % result["repository"]["root"])
    for number, snap in enumerate(result["snapshots"], start=1):
        request = snap["request"]
        if request["kind"] == "index":
            label = "index"
        elif request["kind"] == "rev":
            label = "rev %s" % request["rev"]
        else:
            label = request["kind"]
        if request.get("local_ref"):
            label += " (%s -> %s)" % (request["local_ref"], request.get("remote_ref"))
        info = snap["snapshot"]
        identity = info["commit"] or info["index_digest"] or "-"
        out.append("")
        out.append("snapshot %d: %s [%s %s]" % (number, label, info["kind"], identity))
        if snap["error"] is not None:
            out.append("  TOOL ERROR %s: %s" % (snap["error"]["code"], snap["error"]["message"]))
            out.append("  (no verdict: the checker could not evaluate this snapshot)")
            continue
        for name in checks.DIMENSIONS:
            check = snap["checks"][name]
            if check.get("assessed") is False:
                out.append("  %-24s not assessed" % name)
            else:
                out.append("  %-24s %s  - %s" % (name, check["status"], check["reason"]))
            if name == "DOC_IMPACT_ASSESSMENT" and check.get("assessed"):
                out.append("    report %s sha256 %s, mode %s, profile %s; semantic review: %s"
                           % (check["report"]["path"] or check["report"]["source"],
                              check["report"]["sha256"][:12], check["mode"], check["profile"],
                              check["semantic_review"]))
                for portion in check["portions"]:
                    out.append("    portion %s: %s - %s" % (portion["name"], portion["status"],
                                                            portion["reason"]))
                out.append("    base %s: %s" % (
                    check["base"]["tree"],
                    "verified against the old tip of the push" if check["base"]["independently_verified"]
                    else "NOT independently verified (completeness is relative to this base)"))
                for diag in check["diagnostics"]:
                    out.append("    %s %s: %s%s" % (diag["effect"], diag["code"], diag["message"],
                                                    " [%s]" % diag["where"] if diag["where"] else ""))
            if name == "SPEC_STRUCTURE":
                for track in check["tracks"]:
                    out.append("    track %s (%s): %s, state %s - %s"
                               % (track["track"], track["path"], track["status"],
                                  track["state"], track["reason"]))
                    for diag in track["diagnostics"]:
                        where = ":%d" % diag["line"] if diag["line"] else ""
                        out.append("      %s%s %s: %s" % (track["path"], where,
                                                          diag["code"], diag["message"]))
                for diag in check["diagnostics"]:
                    out.append("    warning %s: %s" % (diag["code"], diag["message"]))
    out.append("")
    summary = result["summary"]
    line = "summary: %s (exit %d)" % (
        summary["status"] if summary["status"] is not None else "NO VERDICT (tool error)",
        summary["exit_code"])
    if "reason" in summary:
        line += " - %s" % summary["reason"]
    out.append(line)
    return "\n".join(out) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)  # usage errors exit 2 via argparse
    if not args.tree and not args.push_stdin:
        print("docops.py: error: give at least one --tree or --push-stdin", file=sys.stderr)
        return 2
    if args.impact_file == "-" and args.push_stdin:
        print("docops.py: error: --impact-file - and --push-stdin both read standard input",
              file=sys.stderr)
        return 2

    try:
        stdin_bytes = sys.stdin.buffer.read() if args.push_stdin else None
        report = _read_impact(args)
        result = _evaluate(args, stdin_bytes, report)
    except ImpactReadError as exc:
        result = checks.build_result(args.command, None, [], str(exc))
    except Exception as exc:  # noqa: BLE001 - fail closed on any checker fault
        result = checks.build_result(
            args.command, None, [], "internal error: %s: %s" % (type(exc).__name__, exc)
        )

    try:
        output = json.dumps(result, indent=2) + "\n" if args.command == "check" \
            else render_text(result)
    except Exception as exc:  # noqa: BLE001 - a rendering fault is a checker fault, not a FAIL
        print("docops.py: internal error while rendering: %s: %s" % (type(exc).__name__, exc),
              file=sys.stderr)
        return 2
    try:
        sys.stdout.write(output)
        sys.stdout.flush()
    except (OSError, ValueError):
        return 2
    return result["summary"]["exit_code"]
