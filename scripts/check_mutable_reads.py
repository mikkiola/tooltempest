#!/usr/bin/env python3
"""Static scan for reads of normative content or executables from a mutable location (ADR-0022).

The defect class: a tool reads a rule document or runs a checker from a place that can drift away from
the commit pinned in the lock (a local clone, the main worktree, the directory a script happens to sit
in), or it judges a read by its path instead of its bytes.

    check_mutable_reads.py [--format text|json] [--clone DIR]... [--allow PATTERN:PATHSUFFIX:REASON]... PATH...
    check_mutable_reads.py --limits

The scan is a bounded heuristic over lines of shell and Python files. It recognizes seven patterns
and nothing else. A clean result means "none of these seven shapes was found", not "the file is safe";
`--limits` prints the forms it does not cover.

    P1  A path inside a local clone of the source repository, written as a literal or reached through a
        variable that holds the clone root, outside a Git object read.
    P2  `cd <clone>` followed by a relative `docs/`, `scripts/`, ... path.
    P3  A path returned by a `resolve_*` / `find_*` style function and then executed, with no hash
        check of that path in between.
    P4  A sibling module imported from the directory the script sits in
        (`sys.path` or `spec_from_file_location` built from `__file__`), in a file without any hash
        check.
    P5  A lookup of a `scripts/` file through `--git-common-dir`, `dirname` of it, or the main worktree.
    P6  A hash is computed, but of a different path than the one that is executed. In Python only the
        file a `subprocess` list runs counts (a variable, or a `.py`/`.sh`/`scripts/` string, after an
        interpreter if there is one); a program found on PATH, such as `git`, is not a pinned file.
    P7  A gate that compares the path of a read with a constant normative path, in a file without any
        content comparison.

`--allow PATTERN:PATHSUFFIX:REASON` is an explicit exclusion: findings of that pattern in files whose path equals
PATHSUFFIX or ends with `/PATHSUFFIX` are printed as `allowed`, with the reason, counted apart, and do not change
the exit code. A blank reason, an unknown pattern, an empty path suffix or a repeated PATTERN:PATHSUFFIX is a usage
error (exit 2); an entry that matches no finding is reported. Nothing is excluded unless the caller names it.

`--clone DIR` adds hygiene lines: a clone that is dirty, detached, or behind its cached upstream ref.
A hygiene warning never changes the exit code. The check runs only local Git reads, with the
`core.fsmonitor` program disabled, and never contacts a remote (nothing is fetched). A `--clone` that is not
the root of a work tree is a tool error (exit 2), not a warning.

Exit code: 0 nothing found, 1 at least one finding, 2 the scan could not give a verdict (usage error,
unreadable path, a `--clone` that is not a repository). Standard library only; nothing is written.
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SCRIPT_SUFFIXES = {".py", ".sh", ".bash", ".zsh"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}

SUB = r"(?:docs|scripts|rules|skills|schemas|MANIFEST)"
HASH_WORDS = re.compile(r"sha256|shasum|hashlib|hexdigest|sha256sum")
# The root of the source clone: the cache path, or a home/workspace based path that ends in `tooltempest`.
CLONE_ROOT = re.compile(
    r"""(?:\.cache/tooltempest/repo|(?:Path\.home\(\)|\$HOME|\$\{HOME\}|~|WORKSPACE|/Users/[^/\s"']+)[^\n]*?[/"'\s]tooltempest(?![\w.-]))"""
)
AFTER_ROOT_SUB = re.compile(r"""["']?\s*[/,]\s*["']?(?:repo["']?\s*[/,]\s*["']?)?""" + SUB + r"\b")
GIT_READ = re.compile(
    r"\bgit\b[^\n]*\b(?:cat-file|show|ls-tree|rev-parse|rev-list|log|status|worktree|diff|ls-files|for-each-ref)\b"
    r"|ObjectStore"
)
OUTPUT_ONLY = re.compile(r"^\s*(?:echo|printf|print)\b")
ASSIGN = re.compile(r"^\s*(?:export\s+|local\s+|readonly\s+)?([A-Za-z_]\w*)\s*=\s*(.*)$")
CD = re.compile(r"""\bcd\s+("[^"]*"|'[^']*'|\S+)""")
REL_PATH = re.compile(r"""(?<![\w/.$"'~-])(?:\./)?""" + SUB + r"""/[\w./-]+""")
RESOLVER_BASH = re.compile(r"""(?:^|[\s!])([A-Za-z_]\w*)=["']?\$\(\s*(?:resolve|find|locate|discover|lookup)_\w*""")
RESOLVER_INLINE = re.compile(
    r"""\b(?:python3?|bash|sh|zsh|source|exec)\s+(?:-\S+\s+)*["']?\$\(\s*(?:resolve|find|locate|discover|lookup)_\w*""")
RESOLVER_PY = re.compile(r"""^\s*([A-Za-z_]\w*)\s*=\s*(?:resolve|find|locate|discover|lookup)_\w*\s*\(""")
SYS_PATH_FILE = re.compile(r"sys\.path\.(?:insert|append)\(.*__file__")
SPEC_FROM = re.compile(r"spec_from_file_location\(")
COMMON_DIR = re.compile(r"--git-common-dir")
MAIN_WORKTREE = re.compile(r"git\s+worktree\s+list[^\n]*\|\s*(?:head|awk|sed|cut)\b")
PATH_CONSTANT = r"\b[A-Z0-9_]*(?:RULES|NORMATIVE|PINNED|DOCS?)[A-Z0-9_]*\b"
P7_LINE = re.compile(
    r"(?:file_path|filepath|\bpath\b)[^\n#]*(?:==|!=|\bin\b)[^\n#]*" + PATH_CONSTANT
    + r"|" + PATH_CONSTANT + r"[^\n#]*(?:==|!=)[^\n#]*(?:file_path|filepath)"
)
CONTENT_PROOF = re.compile(r"sha256|hexdigest|\.digest\(|\bblob\b")
BASH_HASHED = re.compile(r"""(?:shasum|sha256sum)\s+(?:-\w+\s+(?:\d+\s+)?)*("[^"]*"|'[^']*'|[^\s|<>)]+)""")
BASH_EXEC = re.compile(r"""\b(?:python3?|bash|sh|zsh|source|exec)\s+(?:-[BIEsSuvx]+\s+)*("[^"]*"|'[^']*'|[^\s|<>;&)]+)""")
PY_HASHED = re.compile(r"""(?:sha256\w*|hash_file|file_sha256|digest\w*)\(\s*(?:Path\(|open\()?\s*([A-Za-z_][\w.]*)""")
PY_SUBPROCESS_LIST = re.compile(r"""subprocess\.\w+\(\s*\[([^\]]*)\]""")
PY_INTERPRETERS = {"sys.executable", "python", "python3", "bash", "sh", "zsh"}
PY_INTERP_BASE = re.compile(r"python[\d.]*|bash|sh|zsh")
PY_FLAG = re.compile(r"""["']-[\w-]+["']""")
PY_NAME = re.compile(r"[A-Za-z_][\w.]*")
PY_STRING = re.compile(r"""["']([^"']+)["']""")

LIMITS = """Forms the scan does not cover (not covered; a clean result says nothing about them):
- a path built by an interpreter or a program (python -c, perl, node, awk) instead of a shell or Python line
- a path held in a variable that does not hold the clone root, or assembled across several lines or files
- a destination or source reached through a symlink, a config file, an environment variable set elsewhere
- a read or run that is split over a line continuation, a here-document or a generated script
- Git writes (checkout, restore, apply, stash, merge), curl -o, tar -x, ln, rsync, cp -t DIR
- a hash that is computed and then compared to something that is not the pinned value
- a race between the check and the run (the file changes after it was hashed)
- an interpreter executing a file through a wrapper (make, npm scripts, a launcher)
- P6 ignores the leading root variable of a path, so `$A/scripts/x.py` and `$B/scripts/x.py` count as the same
- P6 takes as the executed file of a Python `subprocess` list the first variable or `.py`/`.sh`/`scripts/` string after
  an optional interpreter named `sys.executable`, `python*`, `bash`, `sh` or `zsh` (and the flags and flag values after
  it). It does not recognize: a launcher (`uv run x.py`), an interpreter held in a variable (`[python_bin, tool]`
  reports `python_bin`), an element that is a call, a subscript or an f-string, a list closed by a `]` inside an
  element, a comma or a `#` inside a quoted element, and a plain string that is not a file name (a program on PATH,
  such as `git`, is not a pinned file)
- `--allow` hides a finding of that pattern from the exit code in every scanned file whose path equals the given path
  suffix or ends with "/" and that suffix; an entry that matches nothing is printed, a duplicate entry is a usage error
- a mention of `hashlib` or `sha256` anywhere in a file suppresses P4 for that file; a mention of `sha256`,
  `hexdigest`, `.digest(` or `blob` anywhere in a file suppresses P7 for that file
- a line that starts with echo, printf or print, or that holds the word `git` followed by a Git read word (cat-file,
  show, ls-tree, rev-parse, rev-list, log, status, worktree, diff, ls-files, for-each-ref), or `ObjectStore`, is
  exempt from P1 and P2
- P3 stops looking at the first later line that mentions the resolved variable together with a hash word, whether or
  not that hash is compared
- P7 needs, on one line, `file_path`, `filepath` or `path`, a comparison (`==`, `!=` or `in`), and a constant whose
  name contains RULES, NORMATIVE, PINNED, DOC or DOCS
- a clone path built without a recognizable home, workspace or cache literal (os.path.join of parts)
- a resolver whose name is not resolve_/find_/locate_/discover_/lookup_
- docstrings and string literals are scanned as code, so prose that quotes a pattern is reported
- any language other than shell and Python. A directory walk reads only .py/.sh/.bash/.zsh files and extensionless
  files with a shebang, and skips .git, node_modules, __pycache__, .venv and venv; a file named on the command line is
  always read
- the hygiene check disables only the `core.fsmonitor` program of the clone; a clean filter or another program that the
  clone's own configuration names can still start
The scan finds shapes. It does not prove that a file reads only pinned content."""


class Finding:
    def __init__(self, path: str, line: int, pattern: str, message: str):
        self.path, self.line, self.pattern, self.message = path, line, pattern, message

    def as_dict(self) -> dict:
        return {"path": self.path, "line": self.line, "pattern": self.pattern, "message": self.message}


def _shorten(text: str) -> str:
    text = " ".join(text.strip().split())
    return text if len(text) <= 100 else text[:97] + "..."


def _is_code(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def _norm_word(word: str) -> str:
    word = word.strip().strip("\"'")
    word = re.sub(r"\$\{(\w+)\}", r"$\1", word)
    return re.sub(r"^\$\w+/", "", word)


def _py_exec_target(inner: str) -> Optional[str]:
    """The file a Python `subprocess` list runs. After an interpreter (`sys.executable`, `python3`, `/usr/bin/python3`,
    `bash`, ...) it is the first element that is not a flag, a flag's value or a plain string; without one it is the
    first element. A variable counts (a leading `*` is dropped); a string counts only when it looks like a file
    (`.py`, `.sh`, `scripts/`); a plain string such as "git" is a program on PATH, not a pinned file."""
    elems = [e.strip() for e in inner.split(",") if e.strip()]
    interp = bool(elems) and (elems[0].strip("\"'") in PY_INTERPRETERS or PY_INTERP_BASE.fullmatch(elems[0].strip("\"'").rsplit("/", 1)[-1]))
    for e in elems[1 if interp else 0:]:
        if PY_FLAG.fullmatch(e):
            continue
        name = e.lstrip("*")
        if PY_NAME.fullmatch(name):
            return name
        q = PY_STRING.fullmatch(e)
        if q is None:
            return None  # a call, a subscript, an f-string: not recognized
        if q.group(1).endswith((".py", ".sh")) or "scripts/" in q.group(1):
            return q.group(1)
        if not interp:
            return None  # a program found on PATH
    return None


def scan_text(path: str, text: str) -> List[Finding]:
    lines = text.splitlines()
    code = [(i + 1, l) for i, l in enumerate(lines) if _is_code(l)]
    found: Dict[Tuple[int, str], Finding] = {}

    def add(lineno: int, pattern: str, why: str, raw: str) -> None:
        found.setdefault((lineno, pattern), Finding(path, lineno, pattern, "%s: %s" % (why, _shorten(raw))))

    exempt = lambda l: bool(GIT_READ.search(l) or OUTPUT_ONLY.match(l))  # noqa: E731

    # P1: clone paths and variables that hold the clone root
    clone_vars: List[str] = []
    for n, l in code:
        m = ASSIGN.match(l)
        for r in CLONE_ROOT.finditer(l):
            if AFTER_ROOT_SUB.match(l, r.end()) is None and AFTER_ROOT_SUB.search(l[r.end():r.end() + 40]) is None:
                continue
            if not exempt(l):
                add(n, "P1", "path inside a local clone", l)
            break
        else:
            if m and CLONE_ROOT.search(m.group(2)) and m.group(1) not in clone_vars:
                clone_vars.append(m.group(1))
    for v in clone_vars:
        use = re.compile(r"""(?:\$\{?%s\}?|(?<![\w$])%s)["']?\s*[/,]\s*["']?%s\b""" % (re.escape(v), re.escape(v), SUB))
        for n, l in code:
            if use.search(l) and not exempt(l):
                add(n, "P1", "path inside a local clone through $%s" % v, l)

    # P2: cd into the clone, then a relative path
    for idx, (n, l) in enumerate(code):
        m = CD.search(l)
        if not m:
            continue
        arg = m.group(1)
        in_clone = bool(CLONE_ROOT.search(arg)) or any(re.search(r"\$\{?%s\}?" % re.escape(v), arg) for v in clone_vars)
        if not in_clone:
            continue
        rest = l[m.end():]
        window = [(n, rest)] + [(n2, l2) for n2, l2 in code[idx + 1: idx + 9]]
        for n2, l2 in window:
            if n2 != n and CD.search(l2):
                break
            if not exempt(l2) and REL_PATH.search(l2):
                add(n2, "P2", "relative path after cd into a local clone", l2)

    # P3: a resolved path that is executed without a hash check of that same path
    for n, l in code:
        m = RESOLVER_BASH.search(l) or RESOLVER_PY.match(l)
        if not m:
            continue
        v = m.group(1)
        bash_run = re.compile(
            r"""(?:\b(?:python3?|bash|sh|zsh|source|exec|eval)\s+(?:-\S+\s+)*|^\s*)["']?\$\{?%s\}?""" % re.escape(v))
        py_run = re.compile(
            r"""(?:subprocess\.\w+|os\.exec\w*|runpy\.run_path|spec_from_file_location|\bexec)\s*\([^)]*\b%s\b""" % re.escape(v))
        mention = re.compile(r"""\$\{?%s\}?|\b%s\b""" % (re.escape(v), re.escape(v)))
        for n2, l2 in code:
            if n2 <= n:
                continue
            if HASH_WORDS.search(l2) and mention.search(l2):
                break
            if bash_run.search(l2) or py_run.search(l2):
                add(n2, "P3", "executes a resolved path (%s) with no hash check of it" % v, l2)
                break

    for n, l in code:
        if RESOLVER_INLINE.search(l):
            add(n, "P3", "executes the output of a resolver function with no hash check of it", l)

    # P4: siblings imported from the script's own directory, with no hash facility in the file
    if not re.search(r"hashlib|sha256", text):
        for idx, (n, l) in enumerate(code):
            if SYS_PATH_FILE.search(l):
                add(n, "P4", "module search path built from __file__", l)
            elif SPEC_FROM.search(l):
                window = " ".join(l2 for _, l2 in code[idx: idx + 3])
                if "__file__" in window:
                    add(n, "P4", "module loaded from a path built from __file__", l)

    # P5: lookup of a scripts/ file through the common git dir or the main worktree
    common_vars = []
    for n, l in code:
        m = ASSIGN.match(l)
        if m and COMMON_DIR.search(l):
            common_vars.append(m.group(1))
    for n, l in code:
        if MAIN_WORKTREE.search(l):
            add(n, "P5", "main worktree lookup", l)
        elif COMMON_DIR.search(l) and re.search(r"dirname|/scripts/", l):
            add(n, "P5", "scripts/ path derived from the common git dir", l)
        else:
            for v in common_vars:
                if re.search(r"""dirname\s+["']?\$\{?%s\}?|\$\{?%s\}?/(?:\.\./)?scripts/""" % (re.escape(v), re.escape(v)), l):
                    add(n, "P5", "scripts/ path derived from the common git dir ($%s)" % v, l)

    # P6: the hashed path is not the executed path
    hashed, executed = set(), []
    for n, l in code:
        for m in BASH_HASHED.finditer(l):
            hashed.add(_norm_word(m.group(1)))
        for m in PY_HASHED.finditer(l):
            hashed.add(m.group(1))
        for m in BASH_EXEC.finditer(l):
            w = _norm_word(m.group(1))
            if w.startswith("$") or w.endswith((".py", ".sh")) or "scripts/" in w:
                executed.append((n, w, l))
    code_lines = dict(code)
    starts, pos = [], 0
    for chunk in text.splitlines(keepends=True):  # the same line numbering as `lines`
        starts.append(pos)
        pos += len(chunk)
    for m in PY_SUBPROCESS_LIST.finditer(text):  # a list may span several lines
        n = bisect.bisect_right(starts, m.start())
        target = _py_exec_target(re.sub(r"(?m)(?:^|\s)#[^\n]*", "", m.group(1)))
        if n in code_lines and target is not None:
            executed.append((n, target, code_lines[n]))
    executed.sort(key=lambda e: e[0])
    if hashed and executed and not any(w in hashed for _, w, _ in executed):
        n, w, l = executed[0]
        add(n, "P6", "a hash is computed (%s) but not of the executed path (%s)" % (", ".join(sorted(hashed)), w), l)

    # P7: a gate that judges a read by path
    if not CONTENT_PROOF.search(text):
        for n, l in code:
            if P7_LINE.search(l):
                add(n, "P7", "compares the path of a read, not its content", l)

    return sorted(found.values(), key=lambda f: (f.line, f.pattern))


def _wants(path: Path) -> bool:
    if path.suffix in SCRIPT_SUFFIXES:
        return True
    if path.suffix:
        return False
    try:
        with open(path, "rb") as fh:
            return fh.read(2) == b"#!"
    except OSError:
        return False


def collect(paths: List[str], errors: List[str]) -> List[Path]:
    files: List[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            for root, dirs, names in os.walk(p):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
                for name in sorted(names):
                    q = Path(root) / name
                    if q.is_file() and _wants(q):
                        files.append(q)
        else:
            errors.append("%s: no such file or directory" % raw)
    return files


def _git_env() -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "none",
                "GIT_CONFIG_NOSYSTEM": "1"})
    return env


def _git(clone: str, *args: str) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(["git", "-C", clone, "-c", "core.fsmonitor=false", *args], capture_output=True, text=True, env=_git_env(), timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None


def hygiene(clone: str, errors: List[str]) -> List[dict]:
    top = _git(clone, "rev-parse", "--show-toplevel")
    if top is None or top.returncode != 0 or os.path.realpath(top.stdout.strip()) != os.path.realpath(clone):
        errors.append("%s: not the root of a Git work tree" % clone)
        return []
    out: List[dict] = []
    status = _git(clone, "status", "--porcelain=v1")
    if status is not None and status.stdout.strip():
        out.append({"repo": clone, "kind": "dirty", "detail": "%d changed path(s)" % len(status.stdout.splitlines())})
    sym = _git(clone, "symbolic-ref", "-q", "HEAD")
    if sym is not None and sym.returncode != 0:
        out.append({"repo": clone, "kind": "detached", "detail": "HEAD is not on a branch"})
    else:
        behind = _git(clone, "rev-list", "--count", "HEAD..@{upstream}")
        if behind is not None and behind.returncode == 0 and behind.stdout.strip() not in ("", "0"):
            out.append({"repo": clone, "kind": "behind",
                        "detail": "%s commit(s) behind the cached upstream ref (nothing was fetched)" % behind.stdout.strip()})
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="check_mutable_reads.py", description="Scan for reads from mutable locations.")
    ap.add_argument("paths", nargs="*", metavar="PATH")
    ap.add_argument("--format", choices=("text", "json"), default="text")
    ap.add_argument("--clone", action="append", default=[], metavar="DIR")
    ap.add_argument("--allow", action="append", default=[], metavar="PATTERN:PATHSUFFIX:REASON",
                    help="exclude findings of PATTERN in files whose path ends with PATHSUFFIX, with a reason (listed, not hidden)")
    ap.add_argument("--limits", action="store_true", help="print the forms the scan does not cover and exit")
    args = ap.parse_args(argv)
    if args.limits:
        print(LIMITS)
        return 0
    if not args.paths:
        ap.error("at least one PATH is required")
    allow: Dict[Tuple[str, str], str] = {}
    for spec in args.allow:
        pattern, _, rest = spec.partition(":")
        suffix, _, reason = rest.partition(":")
        suffix = suffix.strip("/")
        if pattern not in ("P%d" % n for n in range(1, 8)) or not suffix or not reason.strip():
            ap.error("--allow needs PATTERN:PATHSUFFIX:REASON with a pattern P1..P7, a path suffix and a reason: %r" % spec)
        if (pattern, suffix) in allow:
            ap.error("--allow given twice for %s:%s" % (pattern, suffix))
        allow[(pattern, suffix)] = reason.strip()

    errors: List[str] = []
    findings: List[Finding] = []
    for f in collect(args.paths, errors):
        try:
            findings.extend(scan_text(str(f), f.read_text(encoding="utf-8", errors="replace")))
        except OSError as exc:
            errors.append("%s: %s" % (f, exc))
    def matching(f: Finding) -> List[Tuple[str, str]]:
        return [(pattern, suffix) for (pattern, suffix) in allow
                if f.pattern == pattern and (f.path == suffix or f.path.endswith("/" + suffix))]

    allowed = [(f, allow[matching(f)[0]]) for f in findings if matching(f)]
    used = {key for f in findings for key in matching(f)}
    unused = sorted("%s:%s" % key for key in allow if key not in used)
    findings = [f for f in findings if not matching(f)]
    hyg: List[dict] = []
    for clone in args.clone:
        hyg.extend(hygiene(clone, errors))

    if args.format == "json":
        print(json.dumps({"findings": [f.as_dict() for f in findings],
                          "allowed": [dict(f.as_dict(), reason=why) for f, why in allowed],
                          "unused_allow": unused,
                          "hygiene": hyg, "errors": errors}, indent=2))
    else:
        for f in findings:
            print("%s:%d: %s: %s" % (f.path, f.line, f.pattern, f.message))
        for f, why in allowed:
            print("%s:%d: allowed %s: %s [reason: %s]" % (f.path, f.line, f.pattern, f.message, why))
        for u in unused:
            print("allow entry matched no finding: %s" % u)
        for h in hyg:
            print("hygiene: %s: %s: %s" % (h["kind"], h["repo"], h["detail"]))
        extra = ", %d allowed" % len(allowed) if allowed else ""
        print("%d finding(s)%s, %d hygiene warning(s)" % (len(findings), extra, len(hyg)))
    for e in errors:
        print("error: " + e, file=sys.stderr)
    if errors:
        return 2
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
