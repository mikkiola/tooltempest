"""Pure SPEC.md analysis: bytes in, verdict out. No I/O.

Two phases (docs/adr/0013):

Phase 1, state: the SPEC's frontmatter alone decides whether the SPEC
declares itself inactive. The body is never searched for the declaration.

    ---
    spec-state: idle
    ---

Frontmatter is recognised only when the very first line is exactly
`---`; it ends at the next line that is exactly `---`. Every line in
between must be blank or flat `key: value` (no indentation, lists or
nesting; this is deliberately not a YAML parser). `spec-state` may
appear at most once and must be `idle` or `active`. A well-formed
`spec-state: idle` is N/A and the body is not parsed. No frontmatter,
or frontmatter that does not declare the key, means active. Present but
malformed frontmatter (unterminated, a non-flat line, a duplicated or
misspelled key, an unknown value, a delimiter-looking first line that is
not exactly `---`) is a FAIL, never "no marker".

Phase 2, body (SPEC not declared idle). The body is parsed only if it
holds none of the constructs this module cannot place exactly, anywhere
in it (the frontmatter region is excluded; closed top-level code fences
are excluded):

  * an HTML comment token (`<!--`, `-->`) or an HTML tag (`<` followed by
    a letter, `/`, `!` or `?`);
  * a line indented four or more columns (tabs count as four);
  * a blockquote marker (`>` after at most three spaces);
  * an invisible or non-ASCII whitespace character (NBSP, zero-width and
    other format characters, line/paragraph separators, control characters
    other than tab);
  * a setext heading underline (`===` / `---` directly under a text line);
  * a nested list: a list marker repeated on a line (`- - x`), a heading
    inside a list item (`- ## x`), or any list item indented one to three
    columns (a nested list; four or more is denied by the indent rule);
  * an indented heading (`##` after one to three spaces), which Markdown
    may place inside the preceding list item;
  * a level-1 or level-2 heading whose title contains markup (`*`, `_`,
    a backtick, `[`, `]`, `&`, `<`, `~`): Markdown renders the title, so the
    literal text no longer says what the heading is, and a section
    boundary or a second "Milestones" could hide behind it;
  This applies to the whole document, not only the Milestones section.

Any of these is a FAIL naming the construct and the line. Nothing about
them is parsed: the module does not try to decide whether a `<!--` is
inside code, a quote or a list, because that is exactly the boundary
detection that earlier designs got wrong.

Fences are supported in one simple form. A fence opens with a run of 3+
backticks or tildes at column 0 and closes with a run of the same
character, at least as long, at column 0, with nothing but spaces or tabs
after it. Everything between is invisible to the checks above and to
milestone counting. A fence-looking line that is indented or follows a
list bullet, a closing-looking run indented one to three columns, and a
fence that is never closed are FAILs.

Inside the Milestones section the rule is an allow-list, not a deny-list:
every non-blank line must be a checkbox item, a sub-heading (`###` and
deeper, at column 0) or plain text. A line that starts like a list item
without being a well-formed checkbox item (an empty bullet, `- text`,
`- [text](url)`, `- [a][b]`, `- \\[x]`, `- > x`, a numbered item), or
that starts with `[`, `\\` or `&`, is a FAIL, because Markdown would
render it as something this module does not count.

If nothing is denied, the structural contract applies: exactly one
level-2 `Milestones` heading whose section, ended by the next level-1 or
level-2 heading, holds at least one top-level checkbox item, each with a
valid marker (`[ ]`, `[x]`, `[X]`), exactly one space or tab between
the bullet and the marker, a space or tab after the marker, and
non-empty same-line text. Items use the bullets `-`, `*` or `+`; ordered
items are not supported in Milestones. Checkboxes outside that section are never
counted.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

STATE_ACTIVE = "active"
STATE_IDLE = "idle"
STATE_UNKNOWN = "unknown"

PASS = "PASS"
FAIL = "FAIL"
NA = "N/A"

STATE_KEY = "spec-state"
_VALID_STATES = (STATE_ACTIVE, STATE_IDLE)
MAX_DIAGNOSTICS = 100

_FRONTMATTER_FENCE = "---"
_FM_LINE_RE = re.compile(r"^([A-Za-z0-9_-]+):(?:[ \t]+(.*))?$")
_NEAR_FRONTMATTER_RE = re.compile(r"^[ \t]*-{3,}")

_FENCE_LIKE_RE = re.compile(
    r"^([ \t]*)((?:(?:[-*+]|[0-9]{1,9}[.)])[ \t]+)*)(`{3,}|~{3,})(.*)$"
)
_ATX_RE = re.compile(r"^(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_INDENTED_HEADING_RE = re.compile(r"^ {1,3}#{1,6}(?:[ \t]|$)")
_HEADING_MARKUP_RE = re.compile(r"[*_`\[\]&<~]")
_MILESTONES_TITLE_RE = re.compile(r"^Milestones(?:$|[\s(:])")
_HTML_RE = re.compile(r"<[A-Za-z/!?]")
_BLOCKQUOTE_RE = re.compile(r"^ {0,3}>")
_SETEXT_UNDERLINE_RE = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")
_BULLET = r"(?:[-*+]|[0-9]{1,9}[.)])[ \t]+"
_NESTED_BULLETS_RE = re.compile(r"^[ \t]*(?:%s){2,}" % _BULLET)
_HEADING_IN_LIST_RE = re.compile(r"^[ \t]*%s#{1,6}(?:[ \t]|$)" % _BULLET)
_INDENTED_LIST_ITEM_RE = re.compile(r"^[ \t]+(?:[-*+]|[0-9]{1,9}[.)])(?:[ \t]|$)")
# A bullet followed by a bracket token of up to three characters is a checkbox
# attempt and must be well formed; longer tokens and links ("[text](url)",
# "[text][ref]") are ordinary list text.
_CHECKBOX_RE = re.compile(r"^[-*+][ \t]\[([^\[\]]{0,3})\](?![(\[])(.*)$")
_LOOSE_CHECKBOX_RE = re.compile(r"^\s*[-*+]\s*\[[^\[\]]{0,3}\](?![(\[])")
_BULLET_LIKE_RE = re.compile(r"^[ \t]*[-*+](?:[ \t]|$)")
_ORDERED_LIKE_RE = re.compile(r"^[ \t]*[0-9]{1,9}[.)](?:[ \t]|$)")
_UNPLACED_START_RE = re.compile(r"^[ \t]*[\[\\&]")
# A list bullet followed by "[" and no "]" on the line: a checkbox cut off or mistyped.
_UNCLOSED_BRACKET_RE = re.compile(r"^[-*+][ \t]*\[[^\]]*$")
_LINE_SPLIT_RE = re.compile(r"\r\n|\r|\n")

_VALID_CHECK_MARKS = (" ", "x", "X")
_INVISIBLE_CATEGORIES = ("Zs", "Zl", "Zp", "Cc", "Cf")


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    line: Optional[int] = None

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "line": self.line}


@dataclass
class SpecVerdict:
    status: str
    state: str
    reason: str
    diagnostics: List[Diagnostic] = field(default_factory=list)
    milestones: Optional[dict] = None


def _decode(data: bytes) -> Tuple[Optional[str], Optional[Diagnostic]]:
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, Diagnostic(
            "ENCODING",
            "SPEC is not valid UTF-8 (byte offset %d: %s)" % (exc.start, exc.reason),
        )
    if "\x00" in text:
        return None, Diagnostic("ENCODING", "SPEC contains NUL characters (binary content)")
    return text, None


# --- phase 1: frontmatter ---------------------------------------------------

def _read_frontmatter(lines: List[str]) -> Tuple[int, Optional[str], List[Diagnostic]]:
    """Return (index of first body line, declared state or None, diagnostics)."""
    if not lines or lines[0] != _FRONTMATTER_FENCE:
        for index, line in enumerate(lines):
            if line.strip(" \t"):
                if _NEAR_FRONTMATTER_RE.match(line):
                    # Looks like a frontmatter opener but is not exactly '---' on line 1:
                    # never silently read as "no frontmatter".
                    return 0, None, [
                        Diagnostic(
                            "FRONTMATTER_NOT_AT_START",
                            "line looks like a frontmatter delimiter but frontmatter must "
                            "start with exactly '---' on the first line",
                            index + 1,
                        )
                    ]
                break
        return 0, None, []
    end = None
    for index in range(1, len(lines)):
        if lines[index] == _FRONTMATTER_FENCE:
            end = index
            break
    if end is None:
        return len(lines), None, [
            Diagnostic(
                "FRONTMATTER_UNTERMINATED",
                "frontmatter opened by '---' on line 1 is never closed by a '---' line",
                1,
            )
        ]

    diagnostics: List[Diagnostic] = []
    declared: List[Tuple[int, str]] = []
    for index in range(1, end):
        line = lines[index]
        number = index + 1
        if not line.strip(" \t"):
            continue
        match = _FM_LINE_RE.match(line)
        if not match:
            diagnostics.append(
                Diagnostic(
                    "FRONTMATTER_LINE",
                    "frontmatter line is not flat 'key: value' (no indentation, lists, "
                    "nesting or odd whitespace)",
                    number,
                )
            )
            continue
        key = match.group(1)
        value = (match.group(2) or "").strip(" \t")
        if key == STATE_KEY:
            declared.append((number, value))
        elif key.lower().replace("_", "-") == STATE_KEY:
            diagnostics.append(
                Diagnostic(
                    "FRONTMATTER_KEY_NEAR_MISS",
                    "key %r looks like %r but is not exactly that" % (key, STATE_KEY),
                    number,
                )
            )

    if len(declared) > 1:
        diagnostics.append(
            Diagnostic(
                "FRONTMATTER_STATE_DUPLICATE",
                "'%s' appears %d times (lines %s); exactly one is allowed"
                % (STATE_KEY, len(declared), ", ".join(str(n) for n, _ in declared)),
                declared[1][0],
            )
        )
    state = None
    if declared:
        number, value = declared[0]
        if value in _VALID_STATES:
            state = value
        else:
            diagnostics.append(
                Diagnostic(
                    "FRONTMATTER_STATE_VALUE",
                    "unknown %s value %r (allowed: active, idle)" % (STATE_KEY, value),
                    number,
                )
            )
    return end + 1, state, diagnostics


# --- phase 2: body ----------------------------------------------------------

def _columns(whitespace: str) -> int:
    return len(whitespace.expandtabs(4))


def _leading(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _is_fence_run(text: str, char: str, minimum: int) -> bool:
    return bool(text) and text == char * len(text) and len(text) >= minimum


class _Diagnostics:
    """Collects diagnostics, bounded so a hostile file cannot flood the result."""

    def __init__(self, sink: List[Diagnostic]):
        self.sink = sink
        self.overflow = 0

    def add(self, code: str, message: str, line: int) -> None:
        if len(self.sink) < MAX_DIAGNOSTICS:
            self.sink.append(Diagnostic(code, message, line))
        else:
            self.overflow += 1

    def close(self) -> None:
        if self.overflow:
            self.sink.append(
                Diagnostic("TOO_MANY_DIAGNOSTICS",
                           "%d further problem(s) not listed" % self.overflow)
            )


def _find_fences(lines: List[str], first_number: int, out: _Diagnostics) -> List[bool]:
    """Mark lines inside (and including) correctly closed top-level fences."""
    hidden = [False] * len(lines)
    index = 0
    total = len(lines)
    while index < total:
        match = _FENCE_LIKE_RE.match(lines[index])
        if match:
            lead, bullets, run, info = match.groups()
            if not (run[0] == "`" and "`" in info):  # "```x```" is inline code, not a fence
                number = first_number + index
                if bullets:
                    out.add("FENCE_NESTED",
                            "code fence after a list bullet is not supported; fences must be "
                            "top level at column 0", number)
                elif lead:
                    out.add("FENCE_INDENTED",
                            "indented code fence is not supported; fences must start at column 0",
                            number)
                else:
                    closer = None
                    for probe in range(index + 1, total):
                        text = lines[probe]
                        core = text.strip(" \t")
                        if not _is_fence_run(core, run[0], len(run)):
                            continue
                        if not _leading(text):
                            closer = probe
                            break
                        if _columns(_leading(text)) <= 3:
                            out.add("FENCE_CLOSER_AMBIGUOUS",
                                    "closing-looking fence run is indented; a closer must be "
                                    "at column 0", first_number + probe)
                            closer = probe
                            break
                    if closer is None:
                        out.add("FENCE_UNCLOSED",
                                "code fence is never closed; everything after it is code",
                                number)
                        for rest in range(index, total):
                            hidden[rest] = True
                        break
                    for rest in range(index, closer + 1):
                        hidden[rest] = True
                    index = closer
        index += 1
    return hidden


def _first_invisible(line: str) -> Optional[str]:
    for char in line:
        if char not in " \t" and unicodedata.category(char) in _INVISIBLE_CATEGORIES:
            return char
    return None


def _analyze_body(lines: List[str], first_number: int, diagnostics: List[Diagnostic]):
    """Deny-list pre-pass over the whole body, then (if nothing was denied) the
    structure scan. Returns (milestone heading lines, items) or ([], []) when
    anything was denied."""
    out = _Diagnostics(diagnostics)
    hidden = _find_fences(lines, first_number, out)

    milestone_headings: List[int] = []
    items: List[Tuple[int, str, str]] = []
    in_milestones = False
    previous: Optional[str] = None  # previous visible non-blank, non-heading line

    for index, line in enumerate(lines):
        if hidden[index]:
            previous = None
            continue
        number = first_number + index

        char = _first_invisible(line)
        if char is not None:
            out.add("DENIED_INVISIBLE",
                    "invisible or non-ASCII whitespace character U+%04X (%s); its effect on "
                    "structure cannot be determined" % (ord(char), unicodedata.name(char, "?")),
                    number)
        if "<!--" in line or "-->" in line:
            out.add("DENIED_COMMENT", "HTML comment token is not supported in SPEC.md", number)
        elif _HTML_RE.search(line):
            out.add("DENIED_HTML", "HTML tag is not supported in SPEC.md", number)
        if _BLOCKQUOTE_RE.match(line):
            out.add("DENIED_BLOCKQUOTE", "blockquote marker is not supported in SPEC.md", number)
        if line.strip(" \t") and _columns(_leading(line)) >= 4:
            out.add("DENIED_INDENT", "line indented four or more columns is not supported in "
                    "SPEC.md", number)
        if previous is not None and _SETEXT_UNDERLINE_RE.match(line):
            out.add("DENIED_SETEXT", "setext heading underline is not supported in SPEC.md",
                    number)
        if _NESTED_BULLETS_RE.match(line):
            out.add("DENIED_NESTED_LIST", "nested list marker on one line is not supported",
                    number)
        elif _INDENTED_LIST_ITEM_RE.match(line):
            out.add("DENIED_NESTED_LIST",
                    "indented list item (a nested list) is not supported in SPEC.md", number)
        elif _HEADING_IN_LIST_RE.match(line):
            out.add("DENIED_NESTED_LIST", "heading inside a list item is not supported", number)

        if _INDENTED_HEADING_RE.match(line):
            out.add("DENIED_INDENTED_HEADING",
                    "heading indented one to three columns is not supported (Markdown may "
                    "place it inside the preceding list item)", number)
            previous = None
            continue

        heading = _ATX_RE.match(line)
        if heading is not None:
            level = len(heading.group(1))
            title = (heading.group(2) or "").strip()
            if level <= 2 and _HEADING_MARKUP_RE.search(title):
                out.add("DENIED_HEADING_MARKUP",
                        "level-1 and level-2 heading titles must be plain text: markup "
                        "(* _ ` [ ] & < ~) changes what Markdown renders as the heading", number)
            if level <= 2:
                in_milestones = False
                if level == 2 and _MILESTONES_TITLE_RE.match(title):
                    milestone_headings.append(number)
                    in_milestones = True
            previous = None
            continue
        previous = line if line.strip(" \t") else None

        if in_milestones:
            box = _CHECKBOX_RE.match(line)
            if box:
                if box.group(2) and box.group(2)[0] not in " \t":
                    out.add("CHECKBOX_SPACING",
                            "checkbox marker must be followed by a space or tab before its text",
                            number)
                items.append((number, box.group(1), box.group(2).strip()))
            elif _ORDERED_LIKE_RE.match(line):
                out.add("DENIED_ORDERED_LIST",
                        "numbered list items are not supported inside Milestones", number)
            elif _LOOSE_CHECKBOX_RE.match(line):
                out.add("AMBIGUOUS_CHECKBOX",
                        "checkbox-looking line is not '<bullet> <space> [mark] <space> text'",
                        number)
            elif _UNCLOSED_BRACKET_RE.match(line):
                out.add("CHECKBOX_UNTERMINATED",
                        "list item starts a checkbox bracket that is never closed (cut off or "
                        "mistyped)", number)
            elif _BULLET_LIKE_RE.match(line) or _UNPLACED_START_RE.match(line):
                out.add("DENIED_UNRECOGNIZED_LINE",
                        "line inside Milestones is neither a well-formed checkbox item, a "
                        "sub-heading nor plain text; Markdown may render it as a list item "
                        "that is not counted", number)

    out.close()
    return milestone_headings, items


def analyze_spec(data: bytes) -> SpecVerdict:
    """Evaluate one SPEC file's bytes. Never raises for content problems."""
    text, enc_diag = _decode(data)
    if text is None:
        return SpecVerdict(FAIL, STATE_UNKNOWN, "SPEC cannot be read as UTF-8 text", [enc_diag])

    lines = _LINE_SPLIT_RE.split(text)
    body_start, state, fm_diagnostics = _read_frontmatter(lines)
    if fm_diagnostics:
        return SpecVerdict(
            FAIL,
            STATE_UNKNOWN,
            "SPEC frontmatter is malformed (%d problem(s))" % len(fm_diagnostics),
            fm_diagnostics,
        )
    if state == STATE_IDLE:
        return SpecVerdict(
            NA,
            STATE_IDLE,
            "SPEC frontmatter declares %s: idle (explicitly no active task)" % STATE_KEY,
        )

    diagnostics: List[Diagnostic] = []
    milestone_headings, items = _analyze_body(lines[body_start:], body_start + 1, diagnostics)
    if diagnostics:
        return SpecVerdict(
            FAIL,
            STATE_ACTIVE,
            "SPEC body holds %d unsupported or ambiguous construct(s); the body was not "
            "parsed for structure" % len(diagnostics),
            diagnostics,
        )

    counts = {
        "total": len(items),
        "checked": sum(1 for _, mark, _ in items if mark in ("x", "X")),
        "unchecked": sum(1 for _, mark, _ in items if mark == " "),
    }
    if not milestone_headings:
        diagnostics.append(
            Diagnostic("MILESTONES_MISSING", "no level-2 '## Milestones' section found")
        )
    elif len(milestone_headings) > 1:
        diagnostics.append(
            Diagnostic(
                "MILESTONES_DUPLICATE",
                "'## Milestones' appears %d times (lines %s); exactly one is allowed"
                % (len(milestone_headings), ", ".join(str(n) for n in milestone_headings)),
                milestone_headings[1],
            )
        )
    if milestone_headings and not items:
        diagnostics.append(
            Diagnostic(
                "MILESTONES_EMPTY",
                "'## Milestones' section has no checkbox list items",
                milestone_headings[0],
            )
        )
    for number, mark, description in items:
        if mark not in _VALID_CHECK_MARKS:
            diagnostics.append(
                Diagnostic(
                    "CHECKBOX_MARKER",
                    "invalid checkbox marker '[%s]' (allowed: '[ ]', '[x]', '[X]')" % mark,
                    number,
                )
            )
        elif not description:
            diagnostics.append(
                Diagnostic("CHECKBOX_EMPTY", "checkbox item has no description text", number)
            )

    if diagnostics:
        return SpecVerdict(
            FAIL,
            STATE_ACTIVE,
            "SPEC violates the structural contract (%d problem(s))" % len(diagnostics),
            diagnostics,
            counts if items else None,
        )
    return SpecVerdict(
        PASS,
        STATE_ACTIVE,
        "active SPEC meets the structural contract (%d milestone item(s))" % counts["total"],
        [],
        counts,
    )
