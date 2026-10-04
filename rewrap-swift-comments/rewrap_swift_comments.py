#!/usr/bin/env python3
"""Greedy-wrap full-line Swift ``//`` and ``///`` comments.

Column width is ``len`` on decoded Unicode text (default 80), not byte
count. Quoted spans, markdown links, doc-link backticks, and URLs are
atomic tokens. URL spans do not count toward the width.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_WIDTH = 80

DEFAULT_ROOTS = (Path("."),)

COMMENT_LINE_RE = re.compile(r"^(\s*)(///|//)(?: (.*))?$")

LIST_ITEM_RE = re.compile(r"^([-*] |\d+\. )(.*)$")

# SPM manifest comments (`swift-tools-version:`) are not prose.
SKIP_FILENAMES = frozenset({"package.swift"})

# SwiftPM checkouts and incremental builds are not project sources.
SKIP_DIR_NAMES = frozenset({".build", ".git"})

# `// LABEL: …` stays one line (INFO / ERROR / MARK / swiftlint / …).
LABEL_LINE_RE = re.compile(r"^[A-Za-z][\w+.-]*:")

# `/// Dark: …` starts a new paragraph. Capitalized so a wrap leftover
# like `identity:` still joins the previous sentence.
DOC_LABEL_LINE_RE = re.compile(r"^[A-Z][\w+.-]*:")

SKIP_SLASH_SLASH_PREFIXES = (
    "swift-format",
)

URL_PREFIXES = ("http://", "https://")

# Periods after a quoted span must not pick up a space: `"foo".`
NO_SPACE_BEFORE = frozenset(".,);:]}!?")

# Keep ``e.g. "sample"`` on one line instead of wrapping after ``e.g.``.
EG_TOKENS = frozenset(("e.g.", "i.e.", "(e.g.", "(i.e."))


def display_len(text: str) -> int:
    """Column count with ``http(s)://`` spans removed (SwiftLint ignores_urls)."""
    length = 0
    i = 0
    n = len(text)
    while i < n:
        if text.startswith(URL_PREFIXES, i):
            j = i
            while j < n and not text[j].isspace():
                j += 1
            while j > i and text[j - 1] in NO_SPACE_BEFORE:
                j -= 1
            i = j
            continue
        length += 1
        i += 1
    return length


def _normalize_markdown_link(token: str) -> str:
    """Collapse whitespace inside ``[label]( dest )`` after a join."""
    close = token.find("](")
    if close == -1 or not token.endswith(")"):
        return token
    dest = " ".join(token[close + 2 : -1].split())
    return f"{token[: close + 2]}{dest})"


def _markdown_link_end(text: str, start: int) -> int | None:
    """Exclusive end of ``[label](url)`` starting at ``start``, or ``None``."""
    if start >= len(text) or text[start] != "[":
        return None
    n = len(text)
    j = start + 1
    depth = 1
    while j < n:
        if text[j] == "[":
            depth += 1
        elif text[j] == "]":
            depth -= 1
            if depth == 0:
                break
        j += 1
    else:
        return None
    if j + 1 >= n or text[j + 1] != "(":
        return None
    k = j + 2
    depth = 1
    while k < n:
        if text[k] == "(":
            depth += 1
        elif text[k] == ")":
            depth -= 1
            if depth == 0:
                return k + 1
        k += 1
    return None


def split_tokens(text: str) -> list[str]:
    """Split on whitespace; keep quotes, links, backticks, and URLs intact."""
    tokens: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        if text[i] == "[":
            end = _markdown_link_end(text, i)
            if end is not None:
                tokens.append(_normalize_markdown_link(text[i:end]))
                i = end
                continue
        if text.startswith("``", i):
            close = text.find("``", i + 2)
            if close == -1:
                tokens.append(text[i:])
                break
            tokens.append(text[i : close + 2])
            i = close + 2
            continue
        if text[i] == "`":
            close = text.find("`", i + 1)
            if close == -1:
                tokens.append(text[i:])
                break
            tokens.append(text[i : close + 1])
            i = close + 1
            continue
        if text[i] == '"':
            close = text.find('"', i + 1)
            if close == -1:
                tokens.append(text[i:])
                break
            tokens.append(text[i : close + 1])
            i = close + 1
            continue
        if text[i] == "“":
            close = text.find("”", i + 1)
            if close == -1:
                tokens.append(text[i:])
                break
            tokens.append(text[i : close + 1])
            i = close + 1
            continue
        if text.startswith(URL_PREFIXES, i):
            j = i
            while j < n and not text[j].isspace():
                j += 1
            tokens.append(text[i:j])
            i = j
            continue
        j = i + 1
        while j < n and not text[j].isspace():
            j += 1
        tokens.append(text[i:j])
        i = j
    return _glue_trailing_punctuation(_glue_eg_quotes(tokens))


def _glue_eg_quotes(tokens: list[str]) -> list[str]:
    glued: list[str] = []
    i = 0
    while i < len(tokens):
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if tokens[i] in EG_TOKENS and nxt[:1] in '"“':
            glued.append(f"{tokens[i]} {nxt}")
            i += 2
            continue
        glued.append(tokens[i])
        i += 1
    return glued


def _glue_trailing_punctuation(tokens: list[str]) -> list[str]:
    """Keep ``,`` / ``.`` on the preceding token so they cannot start a line."""
    glued: list[str] = []
    for token in tokens:
        if glued and token and all(character in NO_SPACE_BEFORE for character in token):
            glued[-1] += token
        else:
            glued.append(token)
    return glued


def is_special_directive(kind: str, text: str) -> bool:
    if kind != "//":
        return False
    stripped = text.lstrip()
    if stripped.startswith(":"):
        return True
    if stripped.startswith(SKIP_SLASH_SLASH_PREFIXES):
        return True
    return LABEL_LINE_RE.match(stripped) is not None


def is_doc_label_line(text: str) -> bool:
    return DOC_LABEL_LINE_RE.match(text.lstrip()) is not None


def list_marker_width(text: str) -> int | None:
    match = LIST_ITEM_RE.match(text)
    if match is None:
        return None
    return len(match.group(1))


def is_hanging_continuation(text: str, hang: int) -> bool:
    """Prose hung under a list marker, not a nested item or deeper sample."""
    if hang <= 0 or not text.startswith(" " * hang):
        return False
    rest = text[hang:]
    if not rest or rest[:1].isspace():
        return False
    return list_marker_width(rest) is None


def is_preformatted(text: str) -> bool:
    return text.startswith("  ")


def wrap_tokens(
    tokens: list[str],
    first_prefix: str,
    cont_prefix: str,
    width: int,
) -> list[str]:
    if not tokens:
        return [first_prefix.rstrip()]

    lines: list[str] = []
    prefix = first_prefix
    current = first_prefix
    for token in tokens:
        if current == prefix:
            trial = current + token
        elif token[:1] in NO_SPACE_BEFORE:
            trial = current + token
        else:
            trial = f"{current} {token}"
        if display_len(trial) <= width:
            current = trial
            continue
        if current != prefix:
            lines.append(current)
        prefix = cont_prefix
        current = cont_prefix + token
        if display_len(current) > width:
            lines.append(current)
            current = prefix
    if current != prefix:
        lines.append(current)
    return lines or [first_prefix.rstrip()]


def wrap_paragraph(text: str, indent: str, kind: str, width: int) -> list[str]:
    lead = f"{indent}{kind} "
    tokens = split_tokens(text)
    list_match = LIST_ITEM_RE.match(text)
    if list_match:
        marker, rest = list_match.group(1), list_match.group(2)
        hang = f"{lead}{' ' * len(marker)}"
        return wrap_tokens(
            split_tokens(f"{marker}{rest}"),
            first_prefix=lead,
            cont_prefix=hang,
            width=width,
        )
    return wrap_tokens(tokens, first_prefix=lead, cont_prefix=lead, width=width)


def parse_comment_line(line: str) -> tuple[str, str, str] | None:
    """Return ``(indent, kind, text)`` for a full-line ``//`` / ``///``.

    ``text`` is ``None``-equivalent empty string for a bare comment line.
    Trailing ``\\n`` must already be stripped.
    """
    match = COMMENT_LINE_RE.fullmatch(line.rstrip("\n"))
    if not match:
        return None
    indent, kind, text = match.group(1), match.group(2), match.group(3)
    return indent, kind, text if text is not None else ""


def rewrite_source(source: str, width: int = DEFAULT_WIDTH) -> str:
    newline = "\r\n" if "\r\n" in source else "\n"
    # Split keeps no newline chars when using splitlines.
    raw_lines = source.splitlines()
    trailing_newline = source.endswith(("\n", "\r\n"))
    out: list[str] = []
    i = 0
    n = len(raw_lines)
    while i < n:
        parsed = parse_comment_line(raw_lines[i])
        if parsed is None:
            out.append(raw_lines[i])
            i += 1
            continue
        indent, kind, _ = parsed
        block: list[tuple[str, str]] = []
        while i < n:
            next_parsed = parse_comment_line(raw_lines[i])
            if next_parsed is None:
                break
            next_indent, next_kind, next_text = next_parsed
            if next_indent != indent or next_kind != kind:
                break
            block.append((raw_lines[i], next_text))
            i += 1
        out.extend(_rewrite_comment_block(block, indent, kind, width))
    text = newline.join(out)
    if trailing_newline:
        text += newline
    return text


def _rewrite_comment_block(
    block: list[tuple[str, str]],
    indent: str,
    kind: str,
    width: int,
) -> list[str]:
    rewritten: list[str] = []
    paragraph_original: list[str] = []
    paragraph_texts: list[str] = []
    list_hang: int | None = None

    def flush() -> None:
        nonlocal paragraph_original, paragraph_texts, list_hang
        if not paragraph_original:
            return
        joined = " ".join(part for part in paragraph_texts if part != "")
        first = paragraph_texts[0] if paragraph_texts else ""
        if (
            is_special_directive(kind, first)
            or any(is_preformatted(part) for part in paragraph_texts)
        ):
            rewritten.extend(paragraph_original)
        else:
            rewritten.extend(wrap_paragraph(joined, indent, kind, width))
        paragraph_original = []
        paragraph_texts = []
        list_hang = None

    for original, text in block:
        if text == "":
            flush()
            rewritten.append(f"{indent}{kind}")
            continue
        if list_hang is not None and is_hanging_continuation(text, list_hang):
            paragraph_original.append(original)
            paragraph_texts.append(text[list_hang:])
            continue
        if is_special_directive(kind, text) or is_preformatted(text):
            flush()
            rewritten.append(original)
            continue
        if paragraph_texts and (
            LIST_ITEM_RE.match(text) or (kind == "///" and is_doc_label_line(text))
        ):
            flush()
        paragraph_original.append(original)
        paragraph_texts.append(text)
        if list_hang is None:
            list_hang = list_marker_width(text)
    flush()
    return rewritten


def is_skipped_swift_path(path: Path) -> bool:
    if path.name.lower() in SKIP_FILENAMES:
        return True
    return any(part in SKIP_DIR_NAMES for part in path.parts)


def iter_swift_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file() and path.suffix == ".swift":
            if not is_skipped_swift_path(path):
                files.append(path)
            continue
        if path.is_dir():
            files.extend(
                candidate
                for candidate in sorted(path.rglob("*.swift"))
                if not is_skipped_swift_path(candidate)
            )
    return files


def process_file(
    path: Path,
    width: int,
    write: bool,
) -> bool:
    original = path.read_text(encoding="utf-8")
    updated = rewrite_source(original, width=width)
    if updated == original:
        return False
    if write:
        path.write_text(updated, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Swift files or directories (default: current directory)",
    )
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if wrapping would change any file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print paths that would change",
    )
    args = parser.parse_args(argv)
    roots = args.paths or [Path(p) for p in DEFAULT_ROOTS]
    files = iter_swift_files(roots)
    write = not args.check and not args.dry_run
    changed = [path for path in files if process_file(path, args.width, write)]
    for path in changed:
        print(path)
    if args.check and changed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
