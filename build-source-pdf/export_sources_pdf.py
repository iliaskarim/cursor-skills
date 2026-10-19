#!/usr/bin/env python3
"""
Build a source listing, a commit patch, or a two-branch divergence PDF.

Run from Swift package root (directory with Package.swift):

    python3 path/to/export_sources_pdf.py
    python3 path/to/export_sources_pdf.py --commit <commit-ish>
    python3 path/to/export_sources_pdf.py --branches develop master

Optional: pass package root as the first argument.
"""

from __future__ import annotations

import argparse
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


_ensure_venv_and_reexec()

from fpdf import FPDF  # noqa: E402
from fpdf.enums import Align, WrapMode  # noqa: E402


def resolve_font() -> Path:
    for p in SF_MONO_BOLD_CANDIDATES:
        if p.is_file():
            return p
    raise FileNotFoundError(
        "Could not find SF-Mono-Bold.otf. Install Xcode or add SF Mono to /Library/Fonts."
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export Package.swift plus Sources/Tests Swift files, a commit patch, "
            "or one combined diff per side of two branches, to a text file and PDF."
        )
    )
    parser.add_argument(
        "package_dir",
        nargs="?",
        help="Swift package root. Defaults to the current directory.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--commit",
        help="Commit-ish whose patch will be exported.",
    )
    mode.add_argument(
        "--branches",
        nargs=2,
        metavar=("LEFT", "RIGHT"),
        help=(
            "Two branches, for example develop and master. "
            "Export one combined diff for the commits on each side."
        ),
    )
    return parser.parse_args()


def _repo_root(package_dir: str | None) -> Path:
    if package_dir:
        return Path(package_dir).expanduser().resolve()
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


# Drop dotfiles and anything under a dot directory (.github, .gitignore, .vscode, …).
_DOTPATH_EXCLUDE = [
    "--",
    ".",
    ":(exclude,glob).*",
    ":(exclude,glob).*/**",
    ":(exclude,glob)**/.*",
    ":(exclude,glob)**/.*/**",
]


def _is_dot_path(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def swift_paths(repo: Path) -> list[Path]:
    out: list[Path] = []
    for pattern in ("Sources/**/*.swift", "Tests/**/*.swift"):
        out.extend(repo.glob(pattern))
    visible = [path for path in out if not _is_dot_path(path.relative_to(repo))]
    return sorted(visible, key=lambda p: p.as_posix())


def git_output(repo: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", "--no-pager", *args],
        cwd=repo,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SystemExit(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def ref_filename_part(ref: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", ref.strip()).strip("-")
    return cleaned or "ref"


def _merge_base(repo: Path, left: str, right: str) -> str | None:
    result = subprocess.run(
        ["git", "--no-pager", "merge-base", left, right],
        cwd=repo,
        text=True,
        capture_output=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    if result.returncode == 1 and not result.stderr.strip():
        return None
    detail = (result.stderr or result.stdout).strip()
    raise SystemExit(f"git merge-base failed: {detail}")


def _commit_counts(repo: Path, left: str, right: str) -> tuple[int, int]:
    raw = git_output(repo, ["rev-list", "--left-right", "--count", f"{left}...{right}"])
    left_count, right_count = raw.split()
    return int(left_count), int(right_count)


def _ruled_section(title: str, body: str) -> str:
    text = body.strip("\n") or "(none)"
    return "\n".join(["=" * FILE_RULE_LEN, title, "=" * FILE_RULE_LEN, "", text, ""])


def _combined_diff(repo: Path, base: str, tip: str) -> str:
    return git_output(repo, ["diff", "--stat", "--patch", f"{base}..{tip}", *_DOTPATH_EXCLUDE])


def build_branch_divergence_text(
    repo: Path,
    title: str,
    left: str,
    left_commit: str,
    right: str,
    right_commit: str,
) -> str:
    base = _merge_base(repo, left_commit, right_commit)
    left_only, right_only = _commit_counts(repo, left_commit, right_commit)
    cover = [
        f"{title} — {left}...{right}",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "",
        f"{left}: {left_commit}",
        f"{right}: {right_commit}",
        f"merge-base: {base or '(unrelated histories)'}",
        "",
        f"{left_only} commits on {left} not in {right}",
        f"{right_only} commits on {right} not in {left}",
    ]
    sections = ["\n".join(cover)]
    if base is None:
        diff = git_output(
            repo,
            ["diff", "--stat", "--patch", left_commit, right_commit, *_DOTPATH_EXCLUDE],
        )
        sections.append(_ruled_section(f"DIFF: {left}..{right}", diff))
    else:
        if left_only:
            sections.append(
                _ruled_section(
                    f"DIFF: {left} since merge-base ({left_only} commits)",
                    _combined_diff(repo, base, left_commit),
                )
            )
        if right_only:
            sections.append(
                _ruled_section(
                    f"DIFF: {right} since merge-base ({right_only} commits)",
                    _combined_diff(repo, base, right_commit),
                )
            )
        if not left_only and not right_only:
            sections.append(_ruled_section(f"DIFF: {left}...{right}", ""))
    return "\f".join(sections) + "\n"


def resolve_commit(repo: Path, commitish: str) -> str:
    return git_output(repo, ["rev-parse", "--verify", f"{commitish}^{{commit}}"]).strip()


def short_commit(repo: Path, commit: str) -> str:
    return git_output(repo, ["rev-parse", "--short", commit]).strip()


def build_commit_patch_text(repo: Path, title: str, commit: str) -> str:
    short = short_commit(repo, commit)
    lines: list[str] = []
    lines.append(f"{title} — commit patch {short}")
    lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    lines.append("")
    patch = git_output(
        repo,
        ["show", "--format=fuller", "--stat", "--patch", commit, *_DOTPATH_EXCLUDE],
    )
    return "\n".join(lines) + "\n" + patch


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
        chunk = section.strip("\n").expandtabs(4)
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
    args = _parse_args()
    repo = _repo_root(args.package_dir)
    slug = _package_title_slug(repo)
    font_path = resolve_font()

    if args.commit:
        commit = resolve_commit(repo, args.commit)
        stem = f"{slug}-commit-{short_commit(repo, commit)}-patch"
        combined = build_commit_patch_text(repo, slug, commit)
    elif args.branches:
        left, right = args.branches
        left_commit = resolve_commit(repo, left)
        right_commit = resolve_commit(repo, right)
        stem = (
            f"{slug}-{ref_filename_part(left)}-{short_commit(repo, left_commit)}"
            f"-vs-{ref_filename_part(right)}-{short_commit(repo, right_commit)}-patch"
        )
        combined = build_branch_divergence_text(
            repo, slug, left, left_commit, right, right_commit
        )
    else:
        paths = swift_paths(repo)
        pkg_swift = repo / "Package.swift"
        has_any = pkg_swift.is_file() or bool(paths)
        if not has_any:
            print("No Package.swift or Swift sources under Sources/ Tests/.", file=sys.stderr)
            return 1

        stem = f"{slug}-all-sources"
        combined = build_combined_text(repo, slug)

    out_txt = repo / f"{stem}.txt"
    out_pdf = repo / f"{stem}.pdf"

    out_txt.write_text(combined, encoding="utf-8")
    write_pdf(combined, font_path, out_pdf)

    print(f"Wrote {out_txt.relative_to(repo)} and {out_pdf.relative_to(repo)} ({font_path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
