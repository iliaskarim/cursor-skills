#!/usr/bin/env python3
"""
Build a source listing, a commit patch, or a two-branch divergence PDF.

Run from Swift package root (directory with Package.swift):

    python3 path/to/export_sources_pdf.py
    python3 path/to/export_sources_pdf.py --files sources
    python3 path/to/export_sources_pdf.py --commit <commit-ish> --files tests
    python3 path/to/export_sources_pdf.py --branches develop master --files all

Optional: pass package root as the first argument.
`--files` is `sources` (Sources/), `tests` (Tests/), or `all`
(every text file except dotfiles: Sources, Tests, Justfile, README, scripts).
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
            "Export a source listing, a commit patch, or one combined diff per side "
            "of two branches, to a text file and PDF."
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
    parser.add_argument(
        "--files",
        choices=("sources", "tests", "all"),
        default="all",
        help=(
            "sources is Sources/. tests is Tests/. "
            "all is every text file except dotfiles, including Justfile, README, and scripts. "
            "Default: all."
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


_SCOPE_TITLE = {"sources": "Sources", "tests": "Tests", "all": "all files"}

# Drop dotfiles and anything under a dot directory (.github, .gitignore, .vscode, …).
_DOT_EXCLUDES = [
    ":(exclude,glob).*",
    ":(exclude,glob).*/**",
    ":(exclude,glob)**/.*",
    ":(exclude,glob)**/.*/**",
]


def _is_dot_path(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def _scope_slug(files: str) -> str:
    return "all-files" if files == "all" else files


def _in_scope(relative: Path, files: str) -> bool:
    if _is_dot_path(relative) or not relative.parts:
        return False
    if files == "sources":
        return relative.parts[0] == "Sources"
    if files == "tests":
        return relative.parts[0] == "Tests"
    return True


def _pathspec(files: str) -> list[str]:
    roots = {"sources": ["Sources"], "tests": ["Tests"], "all": ["."]}[files]
    return ["--", *roots, *_DOT_EXCLUDES]


def _read_text(path: Path) -> str | None:
    data = path.read_bytes()
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def listed_files(repo: Path, files: str) -> list[tuple[Path, str]]:
    found: list[tuple[Path, str]] = []
    for name in git_output(repo, ["ls-files", "-z"]).split("\0"):
        if not name:
            continue
        relative = Path(name)
        if not _in_scope(relative, files):
            continue
        path = repo / relative
        if not path.is_file():
            continue
        text = _read_text(path)
        if text is None:
            continue
        found.append((path, text))
    return sorted(found, key=lambda item: item[0].as_posix())


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


def _combined_diff(repo: Path, base: str, tip: str, files: str) -> str:
    return git_output(
        repo, ["diff", "--stat", "--patch", f"{base}..{tip}", *_pathspec(files)]
    )


def build_branch_divergence_text(
    repo: Path,
    title: str,
    left: str,
    left_commit: str,
    right: str,
    right_commit: str,
    files: str,
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
        f"files: {_SCOPE_TITLE[files]}",
    ]
    sections = ["\n".join(cover)]
    if base is None:
        diff = git_output(
            repo,
            ["diff", "--stat", "--patch", left_commit, right_commit, *_pathspec(files)],
        )
        sections.append(_ruled_section(f"DIFF: {left}..{right}", diff))
    else:
        if left_only:
            sections.append(
                _ruled_section(
                    f"DIFF: {left} since merge-base ({left_only} commits)",
                    _combined_diff(repo, base, left_commit, files),
                )
            )
        if right_only:
            sections.append(
                _ruled_section(
                    f"DIFF: {right} since merge-base ({right_only} commits)",
                    _combined_diff(repo, base, right_commit, files),
                )
            )
        if not left_only and not right_only:
            sections.append(_ruled_section(f"DIFF: {left}...{right}", ""))
    return "\f".join(sections) + "\n"


def resolve_commit(repo: Path, commitish: str) -> str:
    return git_output(repo, ["rev-parse", "--verify", f"{commitish}^{{commit}}"]).strip()


def short_commit(repo: Path, commit: str) -> str:
    return git_output(repo, ["rev-parse", "--short", commit]).strip()


def build_commit_patch_text(repo: Path, title: str, commit: str, files: str) -> str:
    short = short_commit(repo, commit)
    lines: list[str] = []
    lines.append(f"{title} — commit patch {short}")
    lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    lines.append(f"files: {_SCOPE_TITLE[files]}")
    lines.append("")
    patch = git_output(
        repo,
        ["show", "--format=fuller", "--stat", "--patch", commit, *_pathspec(files)],
    )
    return "\n".join(lines) + "\n" + patch


def build_combined_text(repo: Path, title: str, files: str) -> str:
    lines: list[str] = []
    lines.append(f"{title} — {_SCOPE_TITLE[files]}")
    lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    lines.append("")

    parts: list[str] = []
    for path, text in listed_files(repo, files):
        block = [
            "",
            "=" * FILE_RULE_LEN,
            f"FILE: {path.relative_to(repo)}",
            "=" * FILE_RULE_LEN,
            "",
            text,
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

    scope = _scope_slug(args.files)
    if args.commit:
        commit = resolve_commit(repo, args.commit)
        stem = f"{slug}-commit-{short_commit(repo, commit)}-{scope}-patch"
        combined = build_commit_patch_text(repo, slug, commit, args.files)
    elif args.branches:
        left, right = args.branches
        left_commit = resolve_commit(repo, left)
        right_commit = resolve_commit(repo, right)
        stem = (
            f"{slug}-{ref_filename_part(left)}-{short_commit(repo, left_commit)}"
            f"-vs-{ref_filename_part(right)}-{short_commit(repo, right_commit)}-{scope}-patch"
        )
        combined = build_branch_divergence_text(
            repo, slug, left, left_commit, right, right_commit, args.files
        )
    else:
        if not listed_files(repo, args.files):
            print(f"No files for --files {args.files}.", file=sys.stderr)
            return 1
        stem = f"{slug}-{scope}"
        combined = build_combined_text(repo, slug, args.files)

    out_txt = repo / f"{stem}.txt"
    out_pdf = repo / f"{stem}.pdf"

    out_txt.write_text(combined, encoding="utf-8")
    write_pdf(combined, font_path, out_pdf)

    print(f"Wrote {out_txt.relative_to(repo)} and {out_pdf.relative_to(repo)} ({font_path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
