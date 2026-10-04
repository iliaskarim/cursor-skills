#!/usr/bin/env python3
"""Regression tests for rewrap_swift_comments.py."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rewrap_swift_comments as rewrap  # noqa: E402


class TokenTests(unittest.TestCase):
    def test_quoted_span_is_one_token(self) -> None:
        tokens = rewrap.split_tokens('e.g. "owner/repo · #1,234".')
        self.assertEqual(tokens, ['e.g. "owner/repo · #1,234".'])

    def test_doc_link_is_one_token(self) -> None:
        tokens = rewrap.split_tokens("Pair with ``PaginatedResourceList`` for chrome.")
        self.assertEqual(
            tokens,
            ["Pair", "with", "``PaginatedResourceList``", "for", "chrome."],
        )

    def test_url_is_one_token(self) -> None:
        tokens = rewrap.split_tokens("See https://example.com/path?q=1 and more")
        self.assertEqual(
            tokens,
            ["See", "https://example.com/path?q=1", "and", "more"],
        )

    def test_markdown_link_is_one_token(self) -> None:
        link = (
            "[Cancel a workflow run](https://docs.github.com/en/rest/"
            "actions/workflow-runs#cancel-a-workflow-run)"
        )
        tokens = rewrap.split_tokens(f"- SeeAlso: {link}")
        self.assertEqual(tokens, ["-", "SeeAlso:", link])


class WrapTests(unittest.TestCase):
    def test_greedy_fill_then_wrap_at_80(self) -> None:
        source = (
            "/// Binds a pager owned by the caller and runs load / retry "
            "lifecycle. Pair with ``PaginatedResourceList`` for canned list "
            "chrome, or pass a custom builder (news feed, search, detail "
            "activity).\n"
        )
        rewritten = rewrap.rewrite_source(source)
        lines = rewritten.splitlines()
        self.assertEqual(
            lines,
            [
                "/// Binds a pager owned by the caller and runs load / retry lifecycle. Pair with",
                "/// ``PaginatedResourceList`` for canned list chrome, or pass a custom builder",
                "/// (news feed, search, detail activity).",
            ],
        )
        self.assertEqual([len(line) for line in lines], [80, 78, 41])
        self.assertEqual(rewrap.rewrite_source(rewritten), rewritten)

    def test_quoted_sample_does_not_split_after_eg(self) -> None:
        source = (
            "  /// Build via String so the number is not locale-grouped "
            '(e.g. "owner/repo · #1,234").\n'
        )
        lines = rewrap.rewrite_source(source).splitlines()
        quote_lines = [line for line in lines if '"owner/repo · #1,234"' in line]
        self.assertEqual(len(quote_lines), 1)
        self.assertIn("e.g.", quote_lines[0])
        self.assertFalse(any(line.endswith("e.g.") for line in lines))
        for line in lines:
            self.assertLessEqual(len(line), 80)

    def test_unicode_len_not_bytes(self) -> None:
        # en-dash is one column; a byte count would wrap too early.
        text = "PR 404 — deleted, never existed, or unavailable to this token."
        self.assertGreater(len(text.encode("utf-8")), len(text))
        source = f"  /// {text}\n"
        self.assertEqual(rewrap.rewrite_source(source), source)

    def test_paragraph_break_is_kept(self) -> None:
        source = "/// First paragraph word enough to stay.\n///\n/// Second paragraph.\n"
        self.assertEqual(rewrap.rewrite_source(source), source)

    def test_list_items_stay_separate_and_hang(self) -> None:
        words = " ".join(f"word{i}" for i in range(20))
        source = f"/// - {words}\n/// - short\n"
        lines = rewrap.rewrite_source(source).splitlines()
        self.assertTrue(lines[0].startswith("/// - "))
        self.assertTrue(any(line.startswith("///   word") for line in lines))
        self.assertEqual(lines[-1], "/// - short")
        for line in lines:
            self.assertLessEqual(len(line), 80)
        self.assertEqual(rewrap.rewrite_source("\n".join(lines) + "\n"), "\n".join(lines) + "\n")

    def test_short_wrapped_list_item_reflows_to_80(self) -> None:
        source = (
            "/// - **Try searching:** when the query is empty and recents\n"
            "///   are not showing, a static catalog fills the field.\n"
        )
        lines = rewrap.rewrite_source(source).splitlines()
        self.assertEqual(
            lines,
            [
                "/// - **Try searching:** when the query is empty and recents are not showing, a",
                "///   static catalog fills the field.",
            ],
        )
        self.assertLessEqual(len(lines[0]), 80)
        self.assertGreater(len(lines[0]), 70)
        self.assertEqual(rewrap.rewrite_source("\n".join(lines) + "\n"), "\n".join(lines) + "\n")

    def test_nested_list_items_stay_separate(self) -> None:
        source = (
            "/// - Fixture aliases:\n"
            "///   - `code-scanning`\n"
            "///   - `meta-cla`\n"
        )
        self.assertEqual(rewrap.rewrite_source(source), source)

    def test_preformatted_sample_is_left_alone(self) -> None:
        source = "/// Example:\n///   let pager = Pager(endpoint: endpoint)\n"
        self.assertEqual(rewrap.rewrite_source(source), source)

    def test_mark_and_trailing_comments_are_left_alone(self) -> None:
        source = (
            "  // MARK: - Checks\n"
            "  let sha = head  // keep this trailing note here please\n"
            "  // swiftlint:disable:next line_length\n"
        )
        self.assertEqual(rewrap.rewrite_source(source), source)

    def test_comma_stays_with_preceding_doc_link(self) -> None:
        source = (
            "/// Builds a ``URL`` from ``urlScheme``, ``urlHost``, "
            "``urlPort``, ``urlPath``, and ``urlQueryItems``.\n"
        )
        orphaned = (
            "/// Builds a ``URL`` from ``urlScheme``, ``urlHost``, "
            "``urlPort``, ``urlPath``\n"
            "/// , and ``urlQueryItems``.\n"
        )
        for text in (source, orphaned):
            lines = rewrap.rewrite_source(text).splitlines()
            self.assertFalse(
                any(line.removeprefix("///").lstrip().startswith(",") for line in lines)
            )
            self.assertTrue(any(line.endswith("``urlPath``,") for line in lines))
            for line in lines:
                self.assertLessEqual(len(line), 80)

    def test_colon_labeled_slash_slash_lines_are_left_alone(self) -> None:
        source = (
            "// INFO: log response status code and URL.\n"
            "// ERROR: log response line if the status code is non-2xx.\n"
        )
        self.assertEqual(rewrap.rewrite_source(source), source)

    def test_doc_label_line_is_not_joined_with_previous(self) -> None:
        source = (
            "  /// GitHub mobile addition line (light `#d2fedb`).\n"
            "  /// Dark: `--bgColor-success-muted` (`#2ea04326`).\n"
        )
        self.assertEqual(rewrap.rewrite_source(source), source)

    def test_lowercase_colon_continuation_still_joins(self) -> None:
        source = (
            "/// seeds the subtitle when `branch` is nil (default-branch "
            "list). Neither is identity: the same owner/repo/branch/path "
            "compares equal so a clock tap (with a pager and a display "
            "name) is the same as Back from a URL.\n"
        )
        split = (
            "/// seeds the subtitle when `branch` is nil (default-branch list). Neither is\n"
            "/// identity: the same owner/repo/branch/path compares equal so a clock tap\n"
            "/// (with a pager and a display name) is the same as Back from a URL.\n"
        )
        rewritten = rewrap.rewrite_source(split)
        self.assertEqual(rewritten, rewrap.rewrite_source(source))
        self.assertIn("Neither is identity:", rewritten.replace("\n/// ", " "))

    def test_swift_tools_version_comment_is_left_alone(self) -> None:
        source = (
            "// swift-tools-version: 6.0 The swift-tools-version declares "
            "the minimum version of Swift required to build this package.\n"
        )
        self.assertEqual(rewrap.rewrite_source(source), source)

    def test_url_does_not_force_a_wrap(self) -> None:
        url = "https://example.com/" + ("a" * 90)
        source = f"/// See {url} for details.\n"
        lines = rewrap.rewrite_source(source).splitlines()
        self.assertEqual(lines, [f"/// See {url} for details."])
        self.assertLessEqual(rewrap.display_len(lines[0]), 80)
        self.assertGreater(len(lines[0]), 80)

    def test_seealso_markdown_link_stays_one_line(self) -> None:
        source = (
            "/// - SeeAlso: [Cancel a workflow run](https://docs.github.com/"
            "en/rest/actions/workflow-runs#cancel-a-workflow-run)\n"
        )
        expected = source.splitlines()
        self.assertEqual(rewrap.rewrite_source(source).splitlines(), expected)
        self.assertLessEqual(rewrap.display_len(expected[0]), 80)
        self.assertGreater(len(expected[0]), 80)

    def test_seealso_link_split_before_url_is_rejoined(self) -> None:
        url = (
            "https://docs.github.com/en/rest/actions/workflow-jobs"
            "#download-job-logs-for-a-workflow-run"
        )
        source = (
            "/// - SeeAlso: [Download job logs for a workflow run](\n"
            f"///  {url})\n"
        )
        spaced = (
            "/// - SeeAlso: [Download job logs for a workflow run]"
            f"(  {url})\n"
        )
        expected = (
            "/// - SeeAlso: [Download job logs for a workflow run]"
            f"({url})"
        )
        for text in (source, spaced):
            lines = rewrap.rewrite_source(text).splitlines()
            self.assertEqual(lines, [expected])
            self.assertLessEqual(rewrap.display_len(lines[0]), 80)

    def test_oversize_token_stays_one_line(self) -> None:
        token = "``" + ("VeryLongIdentifier" * 6) + "``"
        source = f"/// See {token}\n"
        lines = rewrap.rewrite_source(source).splitlines()
        self.assertEqual(lines[0], "/// See")
        self.assertEqual(lines[1], f"/// {token}")
        self.assertGreater(len(lines[1]), 80)


class FileTests(unittest.TestCase):
    def test_check_exits_one_when_wrap_needed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Sample.swift"
            path.write_text(
                "/// Binds a pager owned by the caller and runs load / retry "
                "lifecycle. Pair with ``PaginatedResourceList`` for canned "
                "list chrome, or pass a custom builder.\n",
                encoding="utf-8",
            )
            self.assertEqual(rewrap.main(["--check", str(path)]), 1)
            rewritten = path.read_text(encoding="utf-8")
            self.assertTrue(rewritten.startswith("/// Binds a pager"))
            self.assertEqual(rewrap.main([str(path)]), 0)
            self.assertEqual(rewrap.main(["--check", str(path)]), 0)

    def test_package_swift_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Package.swift"
            original = (
                "// swift-tools-version: 6.0 The swift-tools-version "
                "declares the minimum version of Swift required to build "
                "this package.\n"
            )
            path.write_text(original, encoding="utf-8")
            self.assertEqual(rewrap.main(["--check", str(path)]), 0)
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertEqual(rewrap.iter_swift_files([path]), [])

    def test_build_dir_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / ".build" / "checkouts" / "HTTPClient"
            nested.mkdir(parents=True)
            path = nested / "Logger.swift"
            path.write_text(
                "/// Binds a pager owned by the caller and runs load / retry "
                "lifecycle. Pair with ``PaginatedResourceList`` for canned "
                "list chrome, or pass a custom builder.\n",
                encoding="utf-8",
            )
            self.assertEqual(rewrap.iter_swift_files([root]), [])
            self.assertEqual(rewrap.iter_swift_files([path]), [])
            self.assertEqual(rewrap.main(["--check", str(root)]), 0)
            self.assertIn("/// Binds a pager", path.read_text(encoding="utf-8"))

    def test_default_root_is_current_directory(self) -> None:
        self.assertEqual(rewrap.DEFAULT_ROOTS, (Path("."),))


if __name__ == "__main__":
    unittest.main()
