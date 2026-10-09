#!/usr/bin/env python3
"""
Build {PackageName}-all-sources.txt and {PackageName}-all-sources.pdf using SF Mono Bold.

Run from Swift package root (directory with Package.swift):

    python3 path/to/export_sources_pdf.py

Optional: pass package root as first argument.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_SKILL_DIR = Path(__file__).resolve().parent
VENV_DIR = _SKILL_DIR / ".venv-export-pdf"
_REQ = _SKILL_DIR / "requirements-export-pdf.txt"
_REEXEC_GUARD = "EXPORT_SOURCES_PDF_REEXEC"

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


def _ensure_venv_and_reexec() -> None:
    if os.environ.get(_REEXEC_GUARD):
        return
    try:
        import fpdf  # noqa: F401
    except ImportError:
        fpdf = None  # type: ignore[assignment]
    if fpdf is not None:
        return

    py = VENV_DIR / "bin" / "python"
    if not py.is_file():
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV_DIR)])
    pip = VENV_DIR / "bin" / "pip"
    subprocess.check_call([str(pip), "install", "-q", "-r", str(_REQ)])
    new_env = {**os.environ, _REEXEC_GUARD: "1"}
    os.execve(str(py), [str(py), str(Path(__file__).resolve()), *sys.argv[1:]], new_env)


def resolve_font() -> Path:
    for p in SF_MONO_BOLD_CANDIDATES:
        if p.is_file():
            return p
    raise FileNotFoundError(
        "Could not find SF-Mono-Bold.otf. Install Xcode or add SF Mono to /Library/Fonts."
    )


def _repo_root() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1]).expanduser().resolve()
    cwd = Path.cwd().resolve()
    if (cwd / "Package.swift").is_file():
        return cwd
    print(
        "Run from your Swift package root (directory containing Package.swift), "
        "or pass it as an argument:",
        file=sys.stderr,
    )
    print(f"  python3 {sys.argv[0]} /path/to/package", file=sys.stderr)
    raise SystemExit(2)


def _package_title_slug(repo: Path) -> str:
    pkg = repo / "Package.swift"
    if not pkg.is_file():
        return repo.name
    text = pkg.read_text(encoding="utf-8")
    m = re.search(r'\bname:\s*"([^"]+)"', text)
    return m.group(1) if m else repo.name


def swift_paths(repo: Path) -> list[Path]:
    out: list[Path] = []
    for pattern in ("Sources/**/*.swift", "Tests/**/*.swift"):
        out.extend(repo.glob(pattern))
    return sorted(out, key=lambda p: p.as_posix())


def build_combined_text(repo: Path, title: str) -> str:
    lines: list[str] = []
    lines.append(f"{title} — full source listing (Package + Sources + Tests)")
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


def write_pdf(text: str, font_path: Path, out_pdf: Path) -> None:
    _ensure_venv_and_reexec()
    from fpdf import FPDF
    from fpdf.enums import Align, WrapMode

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
    repo = _repo_root()
    slug = _package_title_slug(repo)

    paths = swift_paths(repo)
    pkg_swift = repo / "Package.swift"
    has_any = pkg_swift.is_file() or bool(paths)
    if not has_any:
        print("No Package.swift or Swift sources under Sources/ Tests/.", file=sys.stderr)
        return 1

    font_path = resolve_font()

    out_txt = repo / f"{slug}-all-sources.txt"
    out_pdf = repo / f"{slug}-all-sources.pdf"

    combined = build_combined_text(repo, slug)
    out_txt.write_text(combined, encoding="utf-8")
    write_pdf(combined, font_path, out_pdf)

    print(f"Wrote {out_txt.relative_to(repo)} and {out_pdf.relative_to(repo)} ({font_path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
