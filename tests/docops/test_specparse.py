"""Pure SPEC analysis: the four-case policy at the single-file level.

Phase 1 (frontmatter state) and phase 2 (body: deny-list, then structure)
are tested separately. ReviewedExploitTests holds the inputs that earlier
review rounds used to obtain a false PASS or N/A from the superseded
designs; each must FAIL (or stay non-N/A) here.
"""
import random
import re
import unittest

from helpers import DocopsTestCase, GOOD_SPEC, IDLE_SPEC  # noqa: F401  (sys.path setup)

from docops_core.specparse import FAIL, MAX_DIAGNOSTICS, NA, PASS, analyze_spec

FENCE = "`" * 3
NBSP = " "
ZWSP = "​"
BODY = "## Milestones\n\n- [ ] first\n- [x] second\n"  # a valid active body


def fm(*lines):
    """A frontmatter block followed by a valid active body."""
    return "---\n" + "".join(line + "\n" for line in lines) + "---\n" + BODY


def codes(verdict):
    return [d.code for d in verdict.diagnostics]


def run(text):
    return analyze_spec(text.encode("utf-8"))


def ms(*lines):
    """A Milestones section from the given lines."""
    return "## Milestones\n" + "".join(line + "\n" for line in lines)


class ActiveSpecTests(DocopsTestCase):
    def test_valid_active_spec_passes_with_counts(self):
        v = analyze_spec(GOOD_SPEC.encode())
        self.assertEqual((v.status, v.state), (PASS, "active"))
        self.assertEqual(v.milestones, {"total": 2, "checked": 1, "unchecked": 1})

    def test_goals_and_other_section_checkboxes_are_not_milestones(self):
        self.assertEqual(analyze_spec(GOOD_SPEC.encode()).milestones["total"], 2)

    def test_empty_checkbox_outside_milestones_is_not_diagnosed(self):
        text = GOOD_SPEC.replace("- [ ] Goal checkbox that is not a milestone", "- [ ]")
        self.assertEqual(run(text).status, PASS)

    def test_goals_checkboxes_without_milestones_section_fail(self):
        v = run("# S\n\n## Goals\n\n- [ ] a goal\n- [x] another\n")
        self.assertEqual(v.status, FAIL)
        self.assertIn("MILESTONES_MISSING", codes(v))
        self.assertIsNone(v.milestones)

    def test_h3_milestones_heading_does_not_count(self):
        v = run("# S\n\n### Milestones\n\n- [ ] a\n")
        self.assertEqual((v.status, codes(v)), (FAIL, ["MILESTONES_MISSING"]))

    def test_heading_suffix_rules(self):
        for title in ("Milestones", "Milestones (M1-M9)", "Milestones: current", "Milestones — all"):
            self.assertEqual(run("## %s\n- [ ] a\n" % title).status, PASS, title)
        for title in ("Milestones-archive", "Milestones.old", "Milestonesx"):
            self.assertEqual(run("## %s\n- [ ] a\n" % title).status, FAIL, title)

    def test_h3_subsections_stay_inside_milestones(self):
        v = run("## Milestones\n\n### M1: a\n\n- [ ] one\n\n### M2: b\n\n- [x] two\n")
        self.assertEqual((v.status, v.milestones["total"]), (PASS, 2))

    def test_next_h2_and_h1_end_the_section(self):
        for closer in ("## Details", "# Appendix"):
            v = run("## Milestones\n\n- [ ] one\n\n%s\n\n- [ ] not a milestone\n" % closer)
            self.assertEqual((v.status, v.milestones["total"]), (PASS, 1), closer)

    def test_duplicate_empty_and_description_problems_fail(self):
        v = run("## Milestones\n\n- [ ] a\n\n## Milestones\n\n- [ ] b\n")
        self.assertIn("MILESTONES_DUPLICATE", codes(v))
        v = run("## Milestones\n\nNo boxes here.\n\n## Next\n")
        self.assertIn("MILESTONES_EMPTY", codes(v))
        v = run("## Milestones\n\n- [ ] ok\n- [x]\n")
        diag = [d for d in v.diagnostics if d.code == "CHECKBOX_EMPTY"][0]
        self.assertEqual((v.status, diag.line), (FAIL, 4))

    def test_invalid_checkbox_markers_are_diagnosed_not_ignored(self):
        for mark in ("[]", "[y]", "[-]", "[•]", "[xx]", "[ x]", "[~~]", "[x ]", "[  ]", "[Ab1]"):
            v = run("## Milestones\n\n- [ ] ok\n- %s broken\n" % mark)
            self.assertEqual(v.status, FAIL, mark)
            self.assertIn("CHECKBOX_MARKER", codes(v), mark)

    def test_bullet_styles_are_counted(self):
        v = run("## Milestones\n\n- [ ] dash\n* [x] star\n+ [ ] plus\n")
        self.assertEqual((v.status, v.milestones["total"]), (PASS, 3))

    def test_numbered_items_in_milestones_are_denied(self):
        for line in ("1. [ ] numbered", "2) [x] paren", "10. [ ] ten", "2. [ ] foo", "3. plain"):
            v = run("## Milestones\n- [ ] a\n%s\n" % line)
            self.assertEqual(v.status, FAIL, line)
            self.assertIn("DENIED_ORDERED_LIST", codes(v), line)
        # ordered lists elsewhere are fine: they are never counted
        self.assertEqual(run("## Goals\n1. one\n2. two\n\n" + BODY).status, PASS)

    def test_links_and_long_brackets_as_list_items_in_milestones_are_denied(self):
        for line in ("- [text](http://example.invalid) link", "- [text][ref] reference link",
                     "- [a longer bracket phrase] prose", "- [TODO] fix", "- [y][z] bad"):
            v = run("## Milestones\n- [ ] real\n%s\n" % line)
            self.assertEqual(v.status, FAIL, line)
            self.assertIn("DENIED_UNRECOGNIZED_LINE", codes(v), line)

    def test_links_and_brackets_as_plain_paragraph_text_are_fine(self):
        text = ("## Milestones\nSee [the docs](http://example.invalid) and [ref][1] for [details].\n\n"
                "- [ ] real\n")
        v = run(text)
        self.assertEqual((v.status, v.milestones["total"]), (PASS, 1))

    def test_checkbox_spelling_problems_fail(self):
        for line in ("- [ ]text", "- [x]done", "* [X]x", "-[ ] x", "-  [ ] two spaces",
                     "-     [y] five spaces", "- \t [ ] mixed"):
            v = run("## Milestones\n- [ ] ok\n%s\n" % line)
            self.assertEqual(v.status, FAIL, line)
        self.assertEqual(run("## Milestones\n- [ ] ok\n- [x]\ttab ok\n").status, PASS)
        self.assertEqual(run("## Milestones\n- [ ] ok\n-\t[x] tab before marker\n").status, PASS)

    def test_unterminated_checkbox_brackets_fail(self):
        for line in ("- [x", "- [", "- [ text", "* [ ", "+ [ unfinished item"):
            v = run("## Milestones\n- [ ] a\n%s\n" % line)
            self.assertEqual(v.status, FAIL, line)
            self.assertIn("CHECKBOX_UNTERMINATED", codes(v), line)

    def test_truncating_a_valid_spec_inside_a_checkbox_token_never_passes(self):
        full = "## Milestones\n\n- [ ] first\n- [x] second"
        start = full.rindex("- [x]")
        for cut in range(start + 2, start + len("- [x]")):  # "- [" and "- [x": inside the token
            self.assertNotEqual(analyze_spec(full[:cut].encode()).status, PASS, full[:cut])
        self.assertEqual(analyze_spec(full.encode()).status, PASS)
        # cutting right after the marker leaves an item with no text
        self.assertEqual(analyze_spec((full[:start] + "- [x]").encode()).status, FAIL)

    def test_truncating_inside_any_item_line_never_leaves_a_pass_with_a_damaged_item(self):
        full = "## Milestones\n\n- [ ] first item\n- [x] second item\n"
        for cut in range(len("## Milestones\n\n- [ ] first item\n"), len(full)):
            verdict = analyze_spec(full[:cut].encode())
            if verdict.status == PASS:  # only a cut that leaves whole, well-formed items may pass
                for line in full[:cut].split("\n")[2:]:
                    self.assertRegex(line, r"^(- \[[ xX]\] \S.*|)$", full[:cut])

    def test_non_ascii_digits_do_not_make_list_items(self):
        v = run("## Milestones\n- [ ] a\n\uff11. [ ] fullwidth is plain text\n")
        self.assertEqual((v.status, v.milestones["total"]), (PASS, 1))

    def test_lines_markdown_would_render_as_uncounted_items_are_denied(self):
        # Each is a list item (or list-item-like line) in Markdown that is not a checkbox
        # item this module counts; a malformed item could hide behind any of them.
        inputs = {
            "empty bullet + continuation": "- [ ] ok\n\n-\n  [y] bad\n",
            "escaped bracket": "- [ ] ok\n- \\[y] bad\n",
            "character reference": "- [ ] ok\n- &#91;y] bad\n",
            "reference-style bracket": "- [ ] ok\n- [y][z] bad\n",
            "quote inside item": "- [ ] ok\n- > [y] bad\n",
            "numbered after paragraph": "We will do:\n2. [ ] foo\n",
            "bare bracket line": "- [ ] ok\n[y] bad\n",
            "indented bracket line": "- [ ] ok\n  [y] bad\n",
            "escaped line start": "- [ ] ok\n\\- [y] bad\n",
            "entity line start": "- [ ] ok\n&bull; [y] bad\n",
            "plain bullet": "- [ ] ok\n- plain note\n",
        }
        for label, body in inputs.items():
            v = run("## Milestones\n" + body)
            self.assertEqual(v.status, FAIL, label)
            self.assertTrue({"DENIED_UNRECOGNIZED_LINE", "DENIED_ORDERED_LIST", "DENIED_INDENT",
                             "DENIED_NESTED_LIST", "DENIED_BLOCKQUOTE"} & set(codes(v)), label)

    def test_plain_text_bold_emphasis_and_subheadings_inside_milestones_are_fine(self):
        text = ("## Milestones\nIntro with `code`, **bold** and *emphasis* text.\n\n"
                "**Note:** read this.\n\n### M1\n- [ ] one\n  continued text\n"
                "*emphasis* starts a line\n- [x] two\n\n---\n\n+1 means one more\n-- dashes\n")
        v = run(text)
        self.assertEqual((v.status, v.milestones["total"]), (PASS, 2))

    def test_the_allow_list_applies_only_inside_milestones(self):
        text = ("## Goals\n- plain bullet\n[bracket line]\n\\escaped\n&amp; entity\n"
                "2. ordered\n\n" + BODY + "\n## Appendix\n- a bullet\n[x] y\n")
        self.assertEqual(run(text).status, PASS)

    def test_text_comparisons_and_thematic_breaks_are_fine(self):
        text = ("## Milestones\n- [ ] a < b and c <= d, 5 > 3\n\n---\n\n***\n\n## Next\nplain\n"
                "- keep going\n")
        self.assertEqual(run(text).status, PASS)

    def test_empty_and_whitespace_only_files_fail(self):
        for data in (b"", b"\n\n  \n"):
            v = analyze_spec(data)
            self.assertEqual((v.status, v.state), (FAIL, "active"))


class FrontmatterStateTests(DocopsTestCase):
    """Phase 1: the declaration lives in frontmatter, found with plain string splits."""

    def test_idle_in_frontmatter_is_na_and_body_is_not_parsed(self):
        v = analyze_spec(IDLE_SPEC.encode())
        self.assertEqual((v.status, v.state, v.diagnostics), (NA, "idle", []))
        # Even a body that would be invalid, or hold milestones, is not looked at.
        for body in ("", "## Milestones\n- [ ] still here\n", FENCE + "\nunclosed fence\n",
                     "<!-- unclosed comment\n", "garbage \x01 text\n", "> quote\n    indented\n"):
            v = run("---\nspec-state: idle\n---\n" + body)
            self.assertEqual((v.status, v.state), (NA, "idle"), repr(body))

    def test_explicit_active_equals_no_frontmatter(self):
        v = run(fm("spec-state: active"))
        self.assertEqual((v.status, v.state), (PASS, "active"))

    def test_active_declaration_does_not_excuse_a_bad_body(self):
        v = run("---\nspec-state: active\n---\n# S\n")
        self.assertEqual(v.status, FAIL)
        self.assertIn("MILESTONES_MISSING", codes(v))

    def test_frontmatter_without_the_key_means_active(self):
        v = run(fm("id: SPEC-1", "owner: null"))
        self.assertEqual((v.status, v.state), (PASS, "active"))
        self.assertEqual(run("---\nid: SPEC-1\n---\n# S\n").status, FAIL)

    def test_empty_frontmatter_block_means_active(self):
        self.assertEqual(run("---\n---\n" + BODY).status, PASS)

    def test_value_whitespace_and_blank_lines(self):
        self.assertEqual(run("---\n\nspec-state:   idle \t\n\n---\n").status, NA)
        self.assertEqual(run("---\nspec-state:\tidle\n---\n").status, NA)

    def test_duplicate_state_field_is_never_idle(self):
        for lines in (("spec-state: idle", "spec-state: idle"),
                      ("spec-state: idle", "spec-state: active"),
                      ("spec-state: active", "spec-state: idle")):
            v = run(fm(*lines))
            self.assertEqual(v.status, FAIL, lines)  # body is valid: only the duplicate fails it
            self.assertEqual(codes(v), ["FRONTMATTER_STATE_DUPLICATE"])

    def test_unknown_values_are_malformed_not_a_fallback(self):
        for value in ("inactive", "Idle", "IDLE", "none", "true", "idle2", "idle # note",
                      '"idle"', "'idle'", "idle" + NBSP, "", "~", "yes"):
            v = run(fm("spec-state: " + value))
            self.assertEqual(v.status, FAIL, repr(value))
            self.assertEqual(codes(v), ["FRONTMATTER_STATE_VALUE"], repr(value))

    def test_near_miss_keys_fail(self):
        for key in ("spec_state", "Spec-State", "SPEC-STATE", "spec-State", "Spec_State"):
            v = run(fm("%s: idle" % key))
            self.assertEqual(v.status, FAIL, key)
            self.assertEqual(codes(v), ["FRONTMATTER_KEY_NEAR_MISS"], key)

    def test_unterminated_frontmatter_fails(self):
        for text in ("---\nspec-state: idle\n", "---\nspec-state: idle\n" + BODY,
                     "---\n", "---", "---\nspec-state: idle\n--\n", "---\nspec-state: idle\n---x\n"):
            v = run(text)
            self.assertEqual(v.status, FAIL, repr(text))
            self.assertEqual(codes(v), ["FRONTMATTER_UNTERMINATED"], repr(text))

    def test_non_flat_frontmatter_lines_fail(self):
        for bad in ("  nested: x", "\tindented: x", "- list item", "key", "key:value",
                    "# comment", "a b: c", ": value", NBSP, "spec-state:" + NBSP + "idle"):
            v = run(fm("spec-state: active", bad))
            self.assertEqual(v.status, FAIL, repr(bad))
            self.assertIn("FRONTMATTER_LINE", codes(v), repr(bad))

    def test_idle_cannot_hide_malformed_frontmatter_lines(self):
        v = run("---\nspec-state: idle\n  nested: x\n---\n")
        self.assertEqual((v.status, codes(v)), (FAIL, ["FRONTMATTER_LINE"]))

    def test_frontmatter_must_start_on_the_first_line_exactly(self):
        # Recognition is exact; a delimiter-looking first line that is not exact is a
        # FAIL, never a silent fall-back to "active" with the idle declaration lost.
        for opener in ("\n---", " ---", "--- ", "----", "---\t", "\n\n---"):
            for rest in ("spec-state: idle\n---\n# S\n", "spec-state: idle\n---\n" + BODY):
                v = run(opener + "\n" + rest)
                self.assertEqual(v.status, FAIL, repr(opener))
                self.assertEqual(codes(v), ["FRONTMATTER_NOT_AT_START"], repr(opener))

    def test_invisible_characters_before_the_delimiter_never_lose_the_declaration(self):
        for opener in (ZWSP + "---", NBSP + "---", "﻿﻿---", "⁠---", "---" + ZWSP):
            text = opener + "\nspec-state: idle\n\n---\n\n" + BODY
            v = run(text)
            self.assertEqual(v.status, FAIL, repr(opener))
            self.assertTrue({"DENIED_INVISIBLE", "FRONTMATTER_NOT_AT_START"} & set(codes(v)), repr(opener))

    def test_first_line_starting_with_dashes_is_never_silently_ignored(self):
        for first in ("--- spec-state: idle", "---x", "-----a", "---spec-state: idle", "--- # note"):
            v = run(first + "\n\n" + BODY)
            self.assertEqual((v.status, codes(v)), (FAIL, ["FRONTMATTER_NOT_AT_START"]), first)

    def test_ordinary_first_lines_are_not_mistaken_for_frontmatter(self):
        for first in ("# Title", "Some text", "***", "-- not three"):
            self.assertEqual(run(first + "\n\n" + BODY).status, PASS, first)

    def test_closing_line_must_be_exact(self):
        v = run("---\nspec-state: idle\n--- \n" + BODY)
        self.assertEqual(codes(v), ["FRONTMATTER_UNTERMINATED"])

    def test_crlf_bom_and_cr_frontmatter(self):
        self.assertEqual(run(IDLE_SPEC.replace("\n", "\r\n")).status, NA)
        self.assertEqual(run("﻿" + IDLE_SPEC).status, NA)
        self.assertEqual(run(IDLE_SPEC.replace("\n", "\r")).status, NA)

    def test_old_in_body_comment_marker_no_longer_declares_anything(self):
        v = run("<!-- docops:spec-state=idle -->\n# S\n")
        self.assertEqual(v.status, FAIL)
        self.assertIn("DENIED_COMMENT", codes(v))

    def test_state_words_in_the_body_are_just_text(self):
        text = "# S\n\nspec-state: idle\n\n---\n\nspec-state: idle\n\n" + BODY
        self.assertEqual(run(text).status, PASS)

    def test_frontmatter_region_is_excluded_from_the_deny_list(self):
        # Frontmatter has its own exact grammar; a free-text value may hold odd characters.
        v = run("---\nid: a<b>c\nnote: x​y\nspec-state: active\n---\n" + BODY)
        self.assertEqual(v.status, PASS)

    def test_every_truncation_of_idle_frontmatter_is_never_na(self):
        """A SPEC cut off anywhere before its frontmatter is complete must not
        read as idle; the complete block is N/A."""
        full = "---\nspec-state: idle\n---\n# SPEC.md\n".encode()
        complete = full.index(b"---", 4) + 3  # end of the closing '---' line
        for cut in range(0, complete):
            self.assertNotEqual(analyze_spec(full[:cut]).status, NA, "prefix of %d bytes" % cut)
            self.assertNotEqual(analyze_spec(full[:cut]).status, PASS, "prefix of %d bytes" % cut)
        self.assertEqual(analyze_spec(full[:complete]).status, NA)


class DenyListTests(DocopsTestCase):
    """Every denied construct FAILs, anywhere outside a closed fence, naming construct and line."""

    def assertDenied(self, text, code, line=None):
        v = run(text)
        self.assertEqual(v.status, FAIL, text)
        self.assertIn(code, codes(v), text)
        self.assertIsNone(v.milestones)
        if line is not None:
            self.assertIn(line, [d.line for d in v.diagnostics if d.code == code], text)
        self.assertTrue(all(d.message for d in v.diagnostics))
        return v

    def test_html_comment_tokens(self):
        for where in ("<!-- c -->", "text <!-- c", "-->", "<!--", "x --> y", "a<!---->b"):
            self.assertDenied("## Goals\n%s\n\n%s" % (where, BODY), "DENIED_COMMENT", 2)

    def test_html_tags(self):
        for tag in ("<div>", "text <b>bold</b>", "</div>", "<br/>", "<![CDATA[", "<!DOCTYPE html>",
                    "<?php", "<https://example.com>", "<me@example.com>", "<run_date>",
                    "a <span", "- [ ] item <b>x"):
            self.assertDenied("## Goals\n%s\n\n%s" % (tag, BODY), "DENIED_HTML", 2)

    def test_html_inside_backticks_is_still_denied(self):
        # The checker does not parse inline code, so it cannot know the tag is code.
        self.assertDenied("## Goals\nuse `<int>` here\n\n" + BODY, "DENIED_HTML", 2)

    def test_blockquote_markers(self):
        for quote in ("> quoted", ">quoted", "   > three spaces", ">", "> - [ ] fake", "> ## Milestones"):
            self.assertDenied("## Goals\n%s\n\n%s" % (quote, BODY), "DENIED_BLOCKQUOTE", 2)

    def test_greater_than_inside_a_line_is_fine(self):
        self.assertEqual(run("## Goals\nif a > b then c\n\n" + BODY).status, PASS)

    def test_indent_of_four_or_more_columns(self):
        for pad in ("    ", "     ", "\t", "  \t", "          "):
            self.assertDenied("## Goals\n%scontinued text\n\n%s" % (pad, BODY), "DENIED_INDENT", 2)

    def test_indent_of_one_to_three_columns_is_fine(self):
        for pad in (" ", "  ", "   "):
            self.assertEqual(run("## Goals\n%scontinued text\n\n%s" % (pad, BODY)).status, PASS)

    def test_blank_lines_with_whitespace_are_not_indentation(self):
        self.assertEqual(run("## Goals\n        \n\n" + BODY).status, PASS)

    def test_invisible_and_non_ascii_whitespace(self):
        for char in (NBSP, ZWSP, " ", " ", "　", "⁠", "﻿", "­",
                     "\x0b", "\x0c", "\x1b", "‮", "‎", " "):
            for template in ("word%sword", "%s", "%sleading", "trailing%s"):
                text = "## Goals\n" + template % char + "\n\n" + BODY
                self.assertDenied(text, "DENIED_INVISIBLE", 2)

    def test_ordinary_non_ascii_text_is_fine(self):
        self.assertEqual(run("## Goals\nПроверка — 日本語 ✓ café\n\n" + BODY).status, PASS)

    def test_setext_underlines_under_text(self):
        for underline in ("--------", "===", "-", "  ---", "="):
            self.assertDenied("## Goals\nOverview\n%s\n\n%s" % (underline, BODY), "DENIED_SETEXT", 3)
        self.assertDenied(ms("- [ ] a", "---"), "DENIED_SETEXT", 3)
        self.assertDenied("Title\n=====\n" + BODY, "DENIED_SETEXT", 2)

    def test_thematic_breaks_after_blank_or_heading_are_fine(self):
        for text in ("## Goals\ntext\n\n---\n\n" + BODY, "## Goals\n---\n" + BODY,
                     BODY + "\n***\n\n___\n"):
            self.assertEqual(run(text).status, PASS, text)

    def test_nested_list_in_milestones(self):
        for nested in ("  - [ ] sub", " - [ ] sub", "  - sub", "  1. sub", " * sub"):
            self.assertDenied(ms("- [ ] a", nested), "DENIED_NESTED_LIST", 3)

    def test_nested_list_anywhere_in_the_document_is_denied(self):
        # Not scoped to Milestones: scoping would trust the very boundary detection
        # this design stops relying on.
        for nested in ("  - sub", " - sub", "   1. sub", "  * sub"):
            self.assertDenied("## Goals\n- goal\n%s\n\n%s" % (nested, BODY), "DENIED_NESTED_LIST", 3)

    def test_indented_headings_are_denied(self):
        # Markdown may place an indented heading inside the preceding list item, so it
        # cannot be trusted to end the Milestones section.
        for text in ("## Milestones\n- [ ] a\n- goal\n  ## Goals\n- [x]bad\n",
                     "## Milestones\n- [ ] a\n- goal\n\n  ## Goals\n- [x]bad\n",
                     "## Milestones\n- [ ] a\n1. goal\n   ## X\n- [x]bad\n",
                     "## Milestones\n- [ ] a\n # one\n", "## Milestones\n- [ ] a\n  ###### six\n",
                     "## Milestones\n- [ ] a\n   #\n"):
            v = run(text)
            self.assertEqual(v.status, FAIL, text)
            self.assertIn("DENIED_INDENTED_HEADING", codes(v), text)

    def test_column_zero_headings_and_indented_hash_text_are_fine(self):
        self.assertEqual(run("## Milestones\n- [ ] a\n## Next\n  #notaheading\n").status, PASS)
        self.assertEqual(run("## Milestones\n- [ ] a\n  text with # inside\n").status, PASS)

    def test_second_milestones_heading_hidden_by_bold_markup_cannot_pass(self):
        text = "## Milestones\n- [ ] a\n\n## **Milestones**\n- [ ]no-space bad\n"
        v = run(text)
        self.assertEqual(v.status, FAIL)
        self.assertIn("DENIED_HEADING_MARKUP", codes(v))
        self.assertEqual([d.line for d in v.diagnostics if d.code == "DENIED_HEADING_MARKUP"], [4])

    def test_second_milestones_heading_hidden_by_emphasis_markup_cannot_pass(self):
        text = "## Milestones\n- [ ] a\n\n## *Milestones*\n- [ ]no-space bad\n"
        v = run(text)
        self.assertEqual(v.status, FAIL)
        self.assertIn("DENIED_HEADING_MARKUP", codes(v))
        self.assertEqual([d.line for d in v.diagnostics if d.code == "DENIED_HEADING_MARKUP"], [4])

    def test_second_milestones_heading_hidden_by_code_span_markup_cannot_pass(self):
        text = "## Milestones\n- [ ] a\n\n## `Milestones`\n- [ ]no-space bad\n"
        v = run(text)
        self.assertEqual(v.status, FAIL)
        self.assertIn("DENIED_HEADING_MARKUP", codes(v))
        self.assertEqual([d.line for d in v.diagnostics if d.code == "DENIED_HEADING_MARKUP"], [4])

    def test_second_milestones_heading_hidden_by_link_markup_cannot_pass(self):
        text = "## Milestones\n- [ ] a\n\n## [Milestones](#x)\n- [ ]no-space bad\n"
        v = run(text)
        self.assertEqual(v.status, FAIL)
        self.assertIn("DENIED_HEADING_MARKUP", codes(v))
        self.assertEqual([d.line for d in v.diagnostics if d.code == "DENIED_HEADING_MARKUP"], [4])

    def test_second_milestones_heading_hidden_by_character_reference_markup_cannot_pass(self):
        text = "## Milestones\n- [ ] a\n\n## &#77;ilestones\n- [ ]no-space bad\n"
        v = run(text)
        self.assertEqual(v.status, FAIL)
        self.assertIn("DENIED_HEADING_MARKUP", codes(v))
        self.assertEqual([d.line for d in v.diagnostics if d.code == "DENIED_HEADING_MARKUP"], [4])

    def test_formatted_level_one_and_two_headings_are_denied(self):
        # Markdown renders the title, so "**Milestones**" is a second Milestones heading
        # that a literal title match would miss; its section could hold a damaged item.
        for title in ("**Milestones**", "*Milestones*", "`Milestones`", "[Milestones](#x)",
                      "&#77;ilestones", "_Milestones_", "Mile_stones", "Milestones &amp; more",
                      "Milestones [done]", "Milestones <b>x</b>"):
            for level in ("##", "#"):
                text = "## Milestones\n- [ ] a\n\n%s %s\n- [ ]no-space bad\n" % (level, title)
                v = run(text)
                self.assertEqual(v.status, FAIL, (level, title))
                self.assertIn("DENIED_HEADING_MARKUP" if "<" not in title else "DENIED_HTML",
                              codes(v), (level, title))

    def test_plain_headings_and_formatted_deeper_headings_are_fine(self):
        for text in ("## Milestones\n- [ ] a\n\n## Next steps (2026): all - done\nplain\n",
                     "# Title: A Plan\n\n" + BODY,
                     BODY + "\n### **bold** sub-heading with `code` and [link](u)\n",
                     BODY + "\n#### snake_case sub-heading\n"):
            self.assertEqual(run(text).status, PASS, text)

    def test_nested_bullets_on_one_line_and_heading_in_list(self):
        self.assertDenied(ms("- [ ] a", "- - [ ] b"), "DENIED_NESTED_LIST", 3)
        self.assertDenied(ms("- [ ] a", "1. - x"), "DENIED_NESTED_LIST", 3)
        self.assertDenied(ms("- [ ] a", "- ## Milestones", "- [ ] b"), "DENIED_NESTED_LIST", 3)
        self.assertDenied("## Goals\n* # Heading\n\n" + BODY, "DENIED_NESTED_LIST", 2)

    def test_hashtags_in_list_items_are_fine(self):
        self.assertEqual(run(ms("- [ ] tag #release and #1", "- [ ] #hashtag item")).status, PASS)
        self.assertEqual(run("## Goals\n- #hashtag item\n\n" + BODY).status, PASS)

    def test_denied_constructs_are_not_scoped_to_milestones(self):
        # The deny-list applies to the whole document: it does not trust section boundaries.
        for text in ("<!-- c -->\n" + BODY, BODY + "\n<div>\n", "> q\n" + BODY,
                     BODY + "\n## Later\n    indented\n", BODY + "\n## Later\nx" + NBSP + "y\n"):
            self.assertEqual(run(text).status, FAIL, text)

    def test_denied_constructs_report_every_line_up_to_the_cap(self):
        text = "## Goals\n" + "<br>\n" * (MAX_DIAGNOSTICS + 20) + BODY
        v = run(text)
        self.assertEqual(v.status, FAIL)
        self.assertEqual(len([d for d in v.diagnostics if d.code == "DENIED_HTML"]), MAX_DIAGNOSTICS)
        self.assertEqual(v.diagnostics[-1].code, "TOO_MANY_DIAGNOSTICS")

    def test_a_denied_body_is_not_parsed_for_structure(self):
        v = run("<br>\n# no milestones at all\n")
        self.assertEqual(codes(v), ["DENIED_HTML"])  # no MILESTONES_MISSING noise
        self.assertIn("not parsed", v.reason)

    def test_idle_spec_skips_the_deny_list_entirely(self):
        v = run("---\nspec-state: idle\n---\n<div>\n> q\n    x\n" + NBSP + "\n")
        self.assertEqual(v.status, NA)


class FenceTests(DocopsTestCase):
    """One simple form is supported: paired, same character, column 0, top level."""

    def test_closed_fence_containing_checkboxes_is_not_counted(self):
        text = "## Milestones\n- [ ] real\n%smd\n- [ ] fake\n- [y] broken and ignored\n%s\n" % (FENCE, FENCE)
        v = run(text)
        self.assertEqual((v.status, v.milestones["total"]), (PASS, 1))

    def test_everything_inside_a_closed_fence_is_ignored_by_the_deny_list_too(self):
        inside = "<!-- c -->\n<div>\n> quote\n    indented\n" + NBSP + "x\n---\n- - x\n  - nested\n## Milestones"
        text = "## Milestones\n- [ ] real\n%s\n%s\n%s\n- [ ] two\n" % (FENCE, inside, FENCE)
        v = run(text)
        self.assertEqual((v.status, v.milestones["total"]), (PASS, 2))

    def test_closed_fence_hides_headings_too(self):
        text = "%s\n## Milestones\n- [ ] fake\n%s\n\n## Milestones\n- [ ] real\n" % (FENCE, FENCE)
        v = run(text)
        self.assertEqual((v.status, v.milestones["total"]), (PASS, 1))

    def test_tilde_fence_and_longer_closer(self):
        text = "## Milestones\n\n- [ ] real\n\n~~~~\n## Fake\n~~~~~~\n\n- [x] after\n"
        v = run(text)
        self.assertEqual((v.status, v.milestones["total"]), (PASS, 2))

    def test_trailing_spaces_after_opener_info_and_closer(self):
        text = "## Milestones\n- [ ] a\n%sbash {.x}  \n- [ ] fake\n%s \t\n- [ ] b\n" % (FENCE, FENCE)
        self.assertEqual(run(text).milestones["total"], 2)

    def test_inline_triple_backticks_are_not_a_fence(self):
        text = "## Milestones\n- [ ] use %sx%s inline\n- [ ] second\n" % (FENCE, FENCE)
        self.assertEqual(run(text).milestones["total"], 2)
        # ...also when the line starts with them: a backtick in the info text means
        # inline code, so there is no fence to leave unclosed.
        text = "## Milestones\n%sx%s starts the line\n- [ ] first\n- [ ] second\n" % (FENCE, FENCE)
        v = run(text)
        self.assertEqual((v.status, v.milestones["total"]), (PASS, 2))

    def test_other_fence_char_and_shorter_runs_inside_are_content(self):
        text = "## Milestones\n- [ ] a\n````\n%s\n~~~\n- [ ] x\n````\n" % FENCE
        v = run(text)
        self.assertEqual((v.status, v.milestones["total"]), (PASS, 1))

    def test_unclosed_fence_fails(self):
        for text in ("## Milestones\n- [ ] real\n%s\n- [ ] swallowed\n" % FENCE,
                     "%s\n## Milestones\n- [ ] x\n" % FENCE,
                     "## Milestones\n- [ ] a\n~~~\ncode\n"):
            v = run(text)
            self.assertEqual(v.status, FAIL, text)
            self.assertEqual(codes(v), ["FENCE_UNCLOSED"], text)

    def test_mismatched_fences_fail(self):
        for text in ("## Milestones\n- [ ] a\n%s\ncode\n~~~\n- [ ] x\n" % FENCE,   # wrong char
                     "## Milestones\n- [ ] a\n````\ncode\n%s\n" % FENCE,            # closer too short
                     "## Milestones\n- [ ] a\n%s\ncode\n%s trailing\n" % (FENCE, FENCE),  # text after
                     "## Milestones\n- [ ] a\n%s\ncode\n%s%s\n" % (FENCE, FENCE, NBSP)):  # NBSP after
            v = run(text)
            self.assertEqual(v.status, FAIL, text)
            self.assertIn("FENCE_UNCLOSED", codes(v), text)

    def test_indented_and_list_fences_fail(self):
        for opener, code in (("  " + FENCE, "FENCE_INDENTED"), (" " + FENCE, "FENCE_INDENTED"),
                             ("    " + FENCE, "FENCE_INDENTED"), ("\t" + FENCE, "FENCE_INDENTED"),
                             ("- " + FENCE, "FENCE_NESTED"), ("1. " + FENCE, "FENCE_NESTED"),
                             ("- - " + FENCE, "FENCE_NESTED")):
            v = run("## Milestones\n- [ ] a\n%s\ncode\n%s\n- [ ] b\n" % (opener, opener.strip("- 1.\t ") or FENCE))
            self.assertEqual(v.status, FAIL, repr(opener))
            self.assertIn(code, codes(v), repr(opener))

    def test_indented_closing_run_is_ambiguous(self):
        for pad in (" ", "  ", "   "):
            v = run("## Milestones\n- [ ] a\n%s\ncode\n%s%s\n- [ ] b\n" % (FENCE, pad, FENCE))
            self.assertEqual(v.status, FAIL, repr(pad))
            self.assertIn("FENCE_CLOSER_AMBIGUOUS", codes(v), repr(pad))

    def test_run_indented_four_columns_inside_a_fence_is_content(self):
        text = "## Milestones\n- [ ] a\n%s\n    %s\n- [ ] fake\n%s\n- [ ] b\n" % (FENCE, FENCE, FENCE)
        v = run(text)
        self.assertEqual((v.status, v.milestones["total"]), (PASS, 2))

    def test_nbsp_before_a_closing_run_is_not_a_closer(self):
        text = "## Milestones\n- [ ] a\n%s\n%s%s\n- [ ] fake\n" % (FENCE, NBSP, FENCE)
        v = run(text)
        self.assertEqual(v.status, FAIL)
        self.assertIn("FENCE_UNCLOSED", codes(v))

    def test_nbsp_leading_an_opener_is_denied_not_hidden(self):
        v = run("## Milestones\n- [ ] a\n%s%s\n- [ ] fake\n%s\n" % (NBSP, FENCE, FENCE))
        self.assertEqual(v.status, FAIL)
        self.assertIn("DENIED_INVISIBLE", codes(v))

    def test_crlf_inside_fence_closes_correctly(self):
        text = "## Milestones\r\n\r\n- [ ] a\r\n\r\n%s\r\n- [y] fake\r\n%s\r\n" % (FENCE, FENCE)
        self.assertEqual(run(text).status, PASS)


class ReviewedExploitTests(DocopsTestCase):
    """Inputs that obtained a false PASS/N/A from earlier designs; all must FAIL now."""

    def test_comment_hidden_checkbox_variants(self):
        # indented, quoted, code-span-delayed, and comment-then-setext openers
        inputs = [
            "## Milestones\n\n    <!--\n\n- [x]bad\n\n    -->\n\n- [ ] b\n",
            "## Milestones\n\n> %s\n> <!--\n\n- [x]bad\n\n> -->\n\n- [ ] b\n" % FENCE,
            "## Milestones\n\nUse `x\n<!--\ny` here.\n\n- [x]bad\n\n-->\n\n- [ ] b\n",
            "## Milestones\nText <!-- a\n-->\n---\n- [ ] after\n",
            "## Milestones\n- [ ] a\n<!-- `-->`\n## Milestones\n- [ ] b\n-->\n",
            "## Milestones\n- [ ] a\n<!-- x\n--> ` <!-- y\n## Milestones\n- [ ] b\n-->\n",
            "## Milestones\n- [ ] a\n\nMilestones <!-- note\n---\n- [ ] b\n-->\n",
            "## Milestones\n<!-- a\n--> <!-- b --> <!-- c\n- [ ] x\n",
        ]
        for text in inputs:
            v = run(text)
            self.assertEqual(v.status, FAIL, text)
            self.assertTrue({"DENIED_COMMENT", "DENIED_BLOCKQUOTE", "DENIED_INDENT"} & set(codes(v)), text)

    def test_cdata_block(self):
        v = run("## Milestones\n<![CDATA[\n- [ ] only\n]]>\n")
        self.assertEqual((v.status, codes(v)), (FAIL, ["DENIED_HTML"]))

    def test_zero_width_space_before_frontmatter(self):
        v = run(ZWSP + "---\nspec-state: idle\n\n---\n\n## Milestones\n\n- [ ] a\n")
        self.assertEqual(v.status, FAIL)
        self.assertIn("DENIED_INVISIBLE", codes(v))

    def test_heading_nested_in_a_list_item(self):
        v = run("## Milestones\n- [ ] a\n- ## Milestones\n- [ ] b\n")
        self.assertEqual(v.status, FAIL)
        self.assertIn("DENIED_NESTED_LIST", codes(v))

    def test_setext_heading_inside_doubtful_fences_cannot_hide(self):
        for opener, closer in (("    " + FENCE, "    " + FENCE), ("- - " + FENCE, FENCE)):
            text = "## Milestones\n\n- [ ] a\n\n%s\nMilestones\n---\n%s\n" % (opener, closer)
            self.assertEqual(run(text).status, FAIL, opener)

    def test_under_indented_list_fence_closer(self):
        v = run("## Milestones\n1. %s\n%s\n- [WIP] x\n   %s\n- [ ] y\n" % (FENCE, FENCE, FENCE))
        self.assertEqual(v.status, FAIL)

    def test_html_block_and_setext_hiding_items(self):
        self.assertEqual(run("## Milestones\n<div>\n- [ ] x\n</div>\n").status, FAIL)
        self.assertEqual(run("## Milestones\nOverview\n--------\n- [ ] stray item\n").status, FAIL)

    def test_nbsp_closers_and_trailing_nbsp(self):
        self.assertEqual(run("# T\n%s\n%s%s\n## Milestones\n- [ ] fake item\n" % (FENCE, NBSP, FENCE)).status, FAIL)
        self.assertEqual(run("## Milestones\n- [ ] a\n%s\ncode\n%s%s\n- [ ] x\n" % (FENCE, FENCE, NBSP)).status, FAIL)

    def test_indented_checkbox_shown_as_code(self):
        v = run("## Milestones\n\n    - [ ] shown as code\n")
        self.assertEqual(v.status, FAIL)
        self.assertIn("DENIED_INDENT", codes(v))


class EncodingAndLineEndingTests(DocopsTestCase):
    def test_invalid_utf8_fails_with_unknown_state(self):
        for data in (b"## Milestones\n\n- [ ] caf\xe9\n", b"\xff\xfe#\x00 \x00S\x00", b"\x80"):
            v = analyze_spec(data)
            self.assertEqual((v.status, v.state, codes(v)), (FAIL, "unknown", ["ENCODING"]))

    def test_invalid_utf8_in_an_idle_spec_is_not_na(self):
        v = analyze_spec(b"---\nspec-state: idle\n---\n\xc3\x28\n")
        self.assertEqual((v.status, codes(v)), (FAIL, ["ENCODING"]))

    def test_nul_bytes_fail(self):
        self.assertEqual(analyze_spec(GOOD_SPEC.encode() + b"\x00").status, FAIL)
        self.assertEqual(analyze_spec(b"---\nspec-state: idle\n---\n\x00").status, FAIL)

    def test_utf8_bom_is_accepted_once(self):
        self.assertEqual(analyze_spec(b"\xef\xbb\xbf" + GOOD_SPEC.encode()).status, PASS)
        self.assertEqual(analyze_spec(b"\xef\xbb\xbf\xef\xbb\xbf" + GOOD_SPEC.encode()).status, FAIL)

    def test_unicode_content_passes(self):
        self.assertEqual(run("## Milestones\n\n- [ ] Проверка — 日本語 ✓\n").status, PASS)

    def test_crlf_equals_lf(self):
        lf = analyze_spec(GOOD_SPEC.encode())
        crlf = analyze_spec(GOOD_SPEC.replace("\n", "\r\n").encode())
        self.assertEqual((crlf.status, crlf.milestones), (lf.status, lf.milestones))

    def test_crlf_line_numbers_match_lf(self):
        text = "# S\n\n## Milestones\n\n- [ ] ok\n- [x]\n"
        lf = analyze_spec(text.encode())
        crlf = analyze_spec(text.replace("\n", "\r\n").encode())
        self.assertEqual([d.line for d in lf.diagnostics], [d.line for d in crlf.diagnostics])
        self.assertEqual(lf.diagnostics[0].line, 6)

    def test_line_numbers_account_for_frontmatter(self):
        v = run("---\nid: X\n---\n## Milestones\n- [ ] ok\n- [x]\n")
        self.assertEqual([d.line for d in v.diagnostics], [6])
        v = run("---\nid: X\n---\n## Milestones\n- [ ] ok\n<br>\n")
        self.assertEqual([(d.code, d.line) for d in v.diagnostics], [("DENIED_HTML", 6)])

    def test_lone_cr_and_mixed_endings(self):
        v = run("# S\r\r## Milestones\r\n\n- [ ] a\r- [x] b\n")
        self.assertEqual((v.status, v.milestones["total"]), (PASS, 2))


class RobustnessTests(DocopsTestCase):
    POOL = [
        "# Title", "## Milestones", "## Goals", "### M1", "- [ ] item", "- [x] done", "- [ ]",
        "- [q] bad", FENCE, "~~~", "---", "spec-state: idle", "spec-state: active", "id: x",
        "", "plain text", "1. [ ] n", "<!--", "-->", "<!-- note -->", "- [xx] long",
        "    " + FENCE, "- " + FENCE, "    - [ ] deep", NBSP, "`` ` <!--",
        "====", "<div>", "1. " + FENCE, "- [ ]text", "--> <!-- x", "> quote", "  - [ ] sub",
        "- ## Milestones", "- - x", ZWSP + "---", "\t- [ ] tab",
    ]

    def test_random_documents_never_raise_and_na_or_pass_need_their_evidence(self):
        rng = random.Random(1234)
        for _ in range(5000):
            lines = [rng.choice(self.POOL) for _ in range(rng.randint(0, 12))]
            raw = rng.choice(["\n", "\r\n"]).join(lines)
            verdict = analyze_spec(raw.encode())
            self.assertIn(verdict.status, (PASS, FAIL, NA))
            if verdict.status == NA:
                # N/A only ever comes from a well-formed idle declaration in frontmatter.
                self.assertTrue(raw.startswith("---"), raw)
                self.assertIn("spec-state: idle", raw)
                self.assertEqual(verdict.diagnostics, [])
            if verdict.status == PASS:
                self.assertIn("## Milestones", raw)
                self.assertEqual(verdict.diagnostics, [])
                self.assertGreaterEqual(verdict.milestones["total"], 1)

    def test_pass_implies_no_denied_construct_outside_fences(self):
        """Independent oracle: documents without any fence marker that hold a denied
        construct must never PASS. (Written with plain string tests, not the module's regexes.)"""
        rng = random.Random(77)
        denied_lines = ["<!--", "-->", "<div>", "> quote", "    deep", "\tdeep", NBSP + "x", ZWSP,
                        "- - x", "- ## H", "  - [ ] sub", "text <b>x</b>", "  ## Goals", " # H",
                        "Title\n---"]
        plain = ["# Title", "## Milestones", "- [ ] a", "- [x] b", "", "text", "## Goals", "- goal"]
        for _ in range(3000):
            lines = [rng.choice(plain) for _ in range(rng.randint(2, 8))]
            lines.insert(rng.randint(0, len(lines)), rng.choice(denied_lines))
            raw = "\n".join(lines)
            if "## Milestones" not in raw.split("\n"):
                raw = "## Milestones\n" + raw
            self.assertEqual(analyze_spec(raw.encode()).status, FAIL, raw)

    def test_random_bytes_never_raise_and_never_pass(self):
        rng = random.Random(99)
        for _ in range(500):
            raw = bytes(rng.randrange(256) for _ in range(rng.randint(0, 64)))
            self.assertEqual(analyze_spec(raw).status, FAIL)


if __name__ == "__main__":
    unittest.main()
