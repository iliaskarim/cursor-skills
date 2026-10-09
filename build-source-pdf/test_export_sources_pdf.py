#!/usr/bin/env python3
"""Tests for source selection and listing text in export_sources_pdf.py."""

from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import export_sources_pdf as export  # noqa: E402


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _file_block(rel: str, content: str) -> str:
    rule = "=" * export.FILE_RULE_LEN
    return "\n".join(["", rule, f"FILE: {rel}", rule, "", content])


class PackageTitleTests(unittest.TestCase):
    def test_name_comes_from_package_swift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "checkout"
            _write(
                repo / "Package.swift",
                'let package = Package(\n    name: "Demo"\n)\n',
            )
            self.assertEqual(export._package_title_slug(repo), "Demo")

    def test_directory_name_when_package_swift_has_no_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "FallbackName"
            _write(repo / "Package.swift", "// swift-tools-version: 6.0\n")
            self.assertEqual(export._package_title_slug(repo), "FallbackName")

    def test_directory_name_when_package_swift_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "SourcesOnly"
            repo.mkdir()
            self.assertEqual(export._package_title_slug(repo), "SourcesOnly")


class SwiftPathTests(unittest.TestCase):
    def test_only_sources_and_tests_in_posix_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "Pkg"
            _write(repo / "Package.swift", 'let package = Package(name: "Pkg")\n')
            _write(repo / "Sources" / "Zed.swift", "enum Zed {}\n")
            _write(repo / "Sources" / "A" / "A.swift", "enum A {}\n")
            _write(repo / "Sources" / "B.swift", "enum B {}\n")
            _write(repo / "Tests" / "ZedTests.swift", "enum ZedTests {}\n")
            _write(repo / "Scripts" / "Skip.swift", "enum Skip {}\n")
            _write(repo / ".build" / "Sources" / "Hidden.swift", "enum Hidden {}\n")
            rels = [p.relative_to(repo).as_posix() for p in export.swift_paths(repo)]
            self.assertEqual(
                rels,
                [
                    "Sources/A/A.swift",
                    "Sources/B.swift",
                    "Sources/Zed.swift",
                    "Tests/ZedTests.swift",
                ],
            )


class CombinedTextTests(unittest.TestCase):
    def test_listing_order_rules_and_form_feeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "checkout"
            package = 'let package = Package(\n    name: "Demo"\n)\n'
            source_a = "struct A {}\n"
            source_b = "struct B {}\n"
            _write(repo / "Package.swift", package)
            _write(repo / "Sources" / "B.swift", source_b)
            _write(repo / "Sources" / "A.swift", source_a)
            _write(repo / "Other.swift", "struct Other {}\n")
            fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
            with patch.object(export, "datetime") as mock_datetime:
                mock_datetime.now.return_value = fixed
                text = export.build_combined_text(repo, "Demo")

            expected = (
                "Demo — full source listing (Package + Sources + Tests)\n"
                "Generated: 2026-01-02T03:04:05Z\n"
                "\n"
                + "\f".join(
                    [
                        _file_block("Package.swift", package),
                        _file_block("Sources/A.swift", source_a),
                        _file_block("Sources/B.swift", source_b),
                    ]
                )
            )
            self.assertEqual(text, expected)
            self.assertNotIn("Other.swift", text)
            self.assertEqual(text.count("\f"), 2)

    def test_missing_package_swift_omits_that_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "SourcesOnly"
            source = "enum Only {}\n"
            _write(repo / "Sources" / "Only.swift", source)
            fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
            with patch.object(export, "datetime") as mock_datetime:
                mock_datetime.now.return_value = fixed
                text = export.build_combined_text(
                    repo, export._package_title_slug(repo)
                )
            self.assertEqual(
                text,
                "SourcesOnly — full source listing (Package + Sources + Tests)\n"
                "Generated: 2026-01-02T03:04:05Z\n"
                "\n" + _file_block("Sources/Only.swift", source),
            )
            self.assertNotIn("\f", text)


class MainTests(unittest.TestCase):
    def test_empty_package_exits_with_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "Empty"
            _write(repo / "README.md", "no swift\n")
            stderr = StringIO()
            with (
                patch.object(sys, "argv", ["export_sources_pdf.py", str(repo)]),
                redirect_stderr(stderr),
            ):
                code = export.main()
            self.assertEqual(code, 1)
            self.assertIn(
                "No Package.swift or Swift sources under Sources/ Tests/.",
                stderr.getvalue(),
            )
            self.assertEqual(list(repo.glob("*-all-sources.*")), [])


if __name__ == "__main__":
    unittest.main()
