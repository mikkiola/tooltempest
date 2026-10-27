"""Command line surface for scripts/tooling.py: verify, status, materialize.

Exit codes (also in the result's summary.exit_code):

  0  verify: local integrity and upstream provenance are both established;
     status: unless the tool fails (it is informational, findings included);
     materialize: the plan is executable / was applied and verified.
     Freshness never changes the exit code.
  1  something is wrong or unestablished: a missing, edited or unrecorded
     file, a hash that does not match the pinned blob, an unreadable lock,
     manifest or object, an unfinished installation (recovery required), a
     conflict that materialize refuses to overwrite, another materialize
     holding the lock, a platform that lacks what safe writing needs
     (UNSUPPORTED_PLATFORM from the descriptor primitives, checked before the
     lock), or an installation that was applied but could not be verified
     afterwards (POST_INSTALL_VERIFICATION_FAILED). Nothing is ever turned
     into success.
  2  the tool could not run: usage, Git, I/O or internal error, or a platform
     without POSIX file locking (an `error` object is present; a usage error
     from argument parsing is printed on stderr only).

Only `materialize --apply` writes, and only inside the consumer checkout.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import TOOL_VERSION
from .engine import (DEFAULT_SOURCE, Options, ToolFailure, error_document, evaluate, document, run_materialize)
from .objects import GitFailure, GitMissing, run_git


EXIT_CODES_HELP = """exit codes:
  0  verify: local integrity and upstream provenance established; status: unless the
     tool fails; materialize: the plan is executable, or was applied and verified
  1  something is wrong or unestablished, a conflict is refused, the installation
     could not be verified, or the platform cannot write safely
  2  the tool could not run (usage, Git, I/O or internal error; for materialize --apply
     also a platform without POSIX file locking)
"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tooling.py",
        description="Reproduce a consumer's repository-local ToolTempest dependencies from verified "
        "Git objects at the pinned commit.",
        epilog=EXIT_CODES_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version="tooling.py " + TOOL_VERSION)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (
        ("verify", "read-only: report local integrity, upstream provenance and freshness"),
        ("status", "read-only summary of the same properties; exit 0 unless the tool fails"),
        ("materialize", "install the pinned files (dry run unless --apply)"),
    ):
        cmd = sub.add_parser(name, help=text, description=text, epilog=EXIT_CODES_HELP,
                             formatter_class=argparse.RawDescriptionHelpFormatter)
        cmd.add_argument("--root", help="the consumer checkout (default: the Git top level of the "
                         "current directory); a linked worktree works")
        cmd.add_argument("--lock", help="default: <root>/.tooltempest.lock")
        cmd.add_argument("--receipt", help="default: <root>/.tooltempest.receipt.json")
        cmd.add_argument("--source", help="a ToolTempest repository holding the pinned commit "
                         "(default: %s). Only its Git objects are used, never its working tree." % DEFAULT_SOURCE)
        cmd.add_argument("--upstream-ref", metavar="REF", help="local ref in the source that stands for "
                         "'latest upstream' (default: refs/remotes/origin/main, then refs/heads/main)")
        cmd.add_argument("--format", choices=("text", "json"), default="text")
        if name == "materialize":
            cmd.add_argument("--apply", action="store_true",
                             help="actually write files and the receipt (default: dry run)")
            cmd.add_argument("--allow-network", action="store_true",
                             help="with --apply, fetch exactly the pinned commit when it is absent "
                             "locally (never otherwise; a dry run never fetches; verify/status cannot "
                             "be given this)")
    return parser


def _resolve_root(value: Optional[str]) -> str:
    if value:
        root = os.path.realpath(value)
        if not os.path.isdir(root):
            raise ToolFailure("USAGE", "--root %r is not a directory" % value)
        return root
    try:
        top = run_git(["rev-parse", "--show-toplevel"], cwd=os.getcwd()).stdout
    except GitMissing as exc:
        raise ToolFailure("GIT_MISSING", str(exc))
    except GitFailure:
        raise ToolFailure("USAGE", "not inside a Git checkout; pass --root DIR")
    return os.path.realpath(top.decode("utf-8", "surrogateescape").strip())


def main(argv: Optional[List[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    command = args.command
    opts: Optional[Options] = None
    try:
        root = _resolve_root(args.root)
        opts = Options(
            root=root,
            lock_path=os.path.abspath(args.lock) if args.lock else os.path.join(root, ".tooltempest.lock"),
            receipt_path=(os.path.abspath(args.receipt) if args.receipt
                          else os.path.join(root, ".tooltempest.receipt.json")),
            source=os.path.abspath(os.path.expanduser(args.source or DEFAULT_SOURCE)),
            upstream_ref=args.upstream_ref,
            allow_network=bool(getattr(args, "allow_network", False)),
            apply=bool(getattr(args, "apply", False)),
            invoked_as=os.path.abspath(sys.argv[0]),
        )
        if command == "materialize":
            doc = run_materialize(opts)
        else:
            doc = document(evaluate(opts), command)
    except ToolFailure as exc:
        doc = error_document(opts, command, exc.code, exc.message, apply=bool(getattr(args, "apply", False)))
    except Exception as exc:  # noqa: BLE001 - fail closed on any tool fault
        doc = error_document(opts, command, "INTERNAL_ERROR", "%s: %s" % (type(exc).__name__, exc),
                             apply=bool(getattr(args, "apply", False)))
    if args.format == "json":
        sys.stdout.write(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(render_text(doc))
    return doc["summary"]["exit_code"]


# -- text ----------------------------------------------------------------------------------

_LABELS = {"ok": "ok", "failed": "FAILED", "unknown": "not established"}


def render_text(doc: dict) -> str:
    out: List[str] = []
    out.append("tooling %s  (%s)" % (doc["command"], doc["root"]))
    if doc.get("error"):
        out.append("ERROR %s: %s" % (doc["error"]["code"], doc["error"]["message"]))
        out.append("exit 2: the tool could not run; nothing was verified or changed")
        return "\n".join(out) + "\n"
    pin = doc["pin"]
    out.append("pin:  %s" % (pin["commit"] if pin else "none (no usable lock)"))
    if pin:
        out.append("      from %s" % pin["repository"])
    source = doc.get("source")
    if source:
        out.append("source: %s%s" % (source["path"], "" if source["available"] else "  (not available)"))
    summary = doc["summary"]
    out.append("")
    out.append("  local integrity      %s" % _LABELS.get(summary["local_integrity"], summary["local_integrity"]))
    out.append("  upstream provenance  %s" % _LABELS.get(summary["upstream_provenance"], summary["upstream_provenance"]))
    fresh = doc.get("freshness") or {}
    detail = ""
    if fresh.get("status") == "behind":
        detail = "  (%d commit(s) behind %s, as last fetched)" % (fresh["behind"], fresh["upstream_ref"])
    elif fresh.get("reason"):
        detail = "  (%s)" % fresh["reason"]
    out.append("  freshness            %s%s" % (summary["freshness"], detail))
    receipt = doc["receipt"]
    if receipt["present"]:
        out.append("  receipt              %s, commit %s%s" % (
            receipt["state"], receipt["commit"][:12], "" if receipt["matches_pin"] else " (not the pin)"))
    else:
        out.append("  receipt              none")
    out.append("  network              %s" % ("used" if doc["network"]["used"] else (
        "allowed, not needed" if doc["network"]["allowed"] else "not used")))
    if doc["files"]:
        out.append("")
        out.append("files:")
        for record in doc["files"]:
            action = ("  -> %s" % record["action"]) if record.get("action") else ""
            out.append("  %-10s %-12s %s%s" % (record["local"]["status"], record["provenance"],
                                               record["path"], action))
            if record.get("reason"):
                out.append("             %s" % record["reason"])
    if doc["skipped"]:
        out.append("not installed by this tool (%d): global client files and reference documents" % len(doc["skipped"]))
    findings = [f for f in doc["findings"] if f["severity"] != "info"] or []
    infos = [f for f in doc["findings"] if f["severity"] == "info"]
    if findings or infos:
        out.append("")
        out.append("findings:")
        shown = set()
        for finding in findings + infos:
            out.append("  [%s] %s: %s" % (finding["severity"], finding["code"], finding["message"]))
            if finding.get("hint") and finding["hint"] not in shown:
                shown.add(finding["hint"])
                out.append("        %s" % finding["hint"])
    result = doc.get("result")
    out.append("")
    if result is not None:
        if result["dry_run"]:
            actions = {}
            for record in doc["files"]:
                actions[record["action"]] = actions.get(record["action"], 0) + 1
            out.append("dry run: no file and no receipt was written. Planned: %s. Re-run with --apply to install."
                       % (", ".join("%d %s" % (n, a) for a, n in sorted(actions.items())) or "nothing"))
        elif result["applied"]:
            out.append("applied: %d file(s) written%s; verified afterwards: %s" % (
                len(result["written"]), ", receipt updated" if result["receipt_written"] else "",
                "yes" if (result["post_verification"] or {}).get("ok", True) else "NO"))
        else:
            out.append("not applied: %d file(s) written before stopping" % len(result["written"]))
    out.append("exit %d" % summary["exit_code"])
    return "\n".join(out) + "\n"
