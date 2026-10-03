#!/usr/bin/env python3
"""
Build {repo}-all-sources.txt and {repo}-all-sources.pdf using SF Mono Bold.
Run from repo root: python3 ~/.cursor/skills/build-source-pdf/export_sources_pdf.py
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import Align, WrapMode

BODY_PT = 11.0
# Space reserved above bottom for footer + auto page break trigger (fits BODY_PT text).
FOOTER_MARGIN_MM = 20.0

# Width of "====…" rules: must fit SF Mono Bold at BODY_PT within Letter + 10mm margins.
FILE_RULE_LEN = 80

SF_MONO_BOLD_CANDIDATES = (
    Path("/Library/Fonts/SF-Mono-Bold.otf"),
    Path(
        "/Applications/Xcode.app/Contents/SharedFrameworks/"
        "DVTUserInterfaceKit.framework/Versions/A/Resources/Fonts/SF-Mono-Bold.otf"
    ),
)


def repo_root() -> Path:
    path = Path.cwd().resolve()
    for _ in range(10):
        if (path / "Package.swift").is_file():
            return path
        if path.parent == path:
            break
        path = path.parent
    raise RuntimeError("Run from repo root (directory containing Package.swift)")


def resolve_font() -> Path:
    for p in SF_MONO_BOLD_CANDIDATES:
        if p.is_file():
            return p
    raise FileNotFoundError(
        "Could not find SF-Mono-Bold.otf. Install Xcode or add SF Mono to /Library/Fonts."
    )


def swift_paths(repo: Path) -> list[Path]:
    out = subprocess.check_output(
        ["find", "Sources", "Tests", "-name", "*.swift", "-type", "f"],
        cwd=repo,
        text=True,
    )
    paths = [repo / line.strip() for line in out.splitlines() if line.strip()]
    return sorted(paths, key=lambda p: str(p))


def build_combined_text(repo: Path) -> str:
    lines: list[str] = []
    lines.append(f"{repo.name} — full source listing (Package + Sources + Tests)")
    lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    lines.append("")
    parts: list[str] = []
    pkg = repo / "Package.swift"
    if pkg.is_file():
        block = [
            "",
            "=" * FILE_RULE_LEN,
            f"FILE: {pkg.relative_to(repo)}",
            "=" * FILE_RULE_LEN,
            "",
            pkg.read_text(encoding="utf-8"),
        ]
        parts.append("\n".join(block))
    for sp in swift_paths(repo):
        block = [
            "",
            "=" * FILE_RULE_LEN,
            f"FILE: {sp.relative_to(repo)}",
            "=" * FILE_RULE_LEN,
            "",
            sp.read_text(encoding="utf-8"),
        ]
        parts.append("\n".join(block))
    body = "\f".join(parts)
    return "\n".join(lines) + "\n" + body


class SourceListingPDF(FPDF):
    """Letter PDF with SF Mono Bold body and page numbers bottom-left."""

    def __init__(self, font_path: Path) -> None:
        super().__init__(orientation="P", unit="mm", format="Letter")
        self.add_font("SFMonoBold", "", str(font_path))

    def footer(self) -> None:
        self.set_y(-FOOTER_MARGIN_MM)
        self.set_font("SFMonoBold", size=BODY_PT)
        self.set_x(self.l_margin)
        self.cell(0, FOOTER_MARGIN_MM - 2, str(self.page_no()), align="L")


def write_pdf(text: str, font_path: Path, out_pdf: Path) -> None:
    pdf = SourceListingPDF(font_path)
    pdf.set_auto_page_break(auto=True, margin=FOOTER_MARGIN_MM)
    pdf.set_margins(left=10, top=10, right=10)
    pt = BODY_PT
    line_mm = pt * 0.46
    pdf.set_font("SFMonoBold", size=pt)

    sections = text.split("\f")
    first = True
    for section in sections:
        chunk = section.strip("\n")
        if not chunk:
            continue
        if first:
            pdf.add_page()
            first = False
        else:
            pdf.add_page()
        # Default multi_cell is JUSTIFY — it stretches spaces; unusable for code.
        pdf.multi_cell(
            0,
            line_mm,
            chunk,
            align=Align.L,
            wrapmode=WrapMode.CHAR,
            new_x="LMARGIN",
            new_y="NEXT",
        )

    pdf.output(str(out_pdf))


def main() -> int:
    repo = repo_root()
    stem = f"{repo.name}-all-sources"
    out_txt = repo / f"{stem}.txt"
    out_pdf = repo / f"{stem}.pdf"
    font_path = resolve_font()
    combined = build_combined_text(repo)
    out_txt.write_text(combined, encoding="utf-8")
    write_pdf(combined, font_path, out_pdf)
    print(f"Wrote {out_txt.relative_to(repo)} and {out_pdf.relative_to(repo)} ({font_path})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
