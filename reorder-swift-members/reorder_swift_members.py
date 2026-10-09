#!/usr/bin/env python3
"""Reorder Swift type members per project layout rules.

SwiftUI types (View, ViewModifier, ToolbarContent):
  Before ``body``: nested types, then static vars/lets, then static funcs.
  Within each of those groups, sort by ACL, then name. A private nested type
  stays ahead of an internal static var.
  After ``body`` (and the instance-member section on other types): internal instance
  variables, private variables, internal functions, ``init``, ``deinit``, private
  functions. Within the non-private variable group, ACL outranks name (public,
  then package, then internal). A ``subscript`` sorts before the vars of its
  ACL. Exposed stored properties keep relative declaration order within each
  ACL; other vars in that ACL (computed, etc.) insert by name. Other member
  groups sort by ACL, then name.

Other types (struct, class, enum, actor, protocol):
  Nested types, then static vars/lets, then static funcs (ACL, then name
  within each group); then enum cases (declaration order),
  then the same instance-member order as above.
  ``associatedtype`` sorts with nested types, by name. Protocol bodies are
  reordered the same way as struct / class / enum / actor bodies.

Enum cases (every enum, including nested):
  Simple names (no raw / associated value) share one comma-separated
  ``case`` (wrap at 110 columns). Raw-value, associated-value, and
  commented cases stay their own declarations, with a blank line between
  ``case`` statements.

File layout (per file, after imports):
  private/fileprivate helpers (sorted by name; ``Type?`` follows ``Type``),
  main type(s), other extensions, previews.
  A private/fileprivate extension whose head names the file's type stays with
  the other extensions. Extensions that share a root type stay together, parent
  before nested types (``extension Issue`` above ``extension Issue.Label`` and
  ``extension Issue.User``). Distinct roots keep the order they first appear.
  A conformance (``extension Issue: ConversationRecord``) is not sugar.
  Collection or optional sugar
  (``private extension [Board.Vector]``, ``Board.Vector?``) sorts after the
  file type's own extensions, so it stays below ``extension Board: Collection``.
  It is not a preamble helper. Unrelated private extensions still sort into
  the preamble.
  A ``protocol`` is a main type, same as ``struct`` / ``class`` / ``enum`` / ``actor``,
  so conformances stay below the protocol the file is named for.
  Import-only ``#if`` / ``#endif`` blocks (``#if DEBUG``, ``#if canImport``, …)
  stay in the import preamble. They must not be left behind when a private
  helper is hoisted, and they must not be ranked as leftover chunks.
  ``#Preview`` chunks that contain ``\"\"\"`` stay opaque; other top-level
  chunks in the file still reorder. Adjacent file-scope chunks that share
  a pure ``#if DEBUG`` stay in one wrapper (no ``#endif`` / ``#if DEBUG``
  between a preview helper and its fixtures, or between ``#Preview`` blocks).

Brace and paren matching treat ``\"\"\"…\"\"\"``, ordinary ``\"…\"``, and
raw ``#\"…\"#`` / ``#\"\"\"…\"\"\"#`` literals as opaque so embedded HTML /
JS / GraphQL does not split members.

Does not write a file that still has merge conflict markers, whose ``{`` / ``}``
counts would change, or whose file-layout pass would hoist instance members
(``func`` / ``init`` / ``deinit`` / ``var`` / ``let``) to file scope.

Also prefers ``.init`` over explicit type names where Swift can already infer
the type (property-wrapper ``wrappedValue:`` labels, existing typed
assignments, and constructors of the enclosing type). Does not add a
``: Type`` annotation so a constructor can become ``.init``. Static factories
that return that type are rewritten to ``Self`` / ``Self?``.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ACL_RANK = {
    "public": 0,
    "package": 1,
    "internal": 2,
    "fileprivate": 3,
    "private": 4,
}

BEFORE_BODY_KINDS = ("type", "static_var", "static_fn")
# Within each ACL: nested types, then vars/lets, then funcs.
BEFORE_BODY_MEMBER_RANK = {"type": 0, "static_var": 1, "static_fn": 2}
VAR_BEFORE_FN_RANK = {"instance_var": 0, "static_var": 0, "instance_fn": 1, "static_fn": 1}
AFTER_BODY_KINDS = ("instance_var", "instance_fn")
# SwiftLint ``line_length`` warning. Use Unicode code points (``len`` on ``str``).
ENUM_CASE_LINE_LENGTH = 110

SWIFTUI_CONFORMANCE = re.compile(
    r":\s*(?:[\w.<>,\s]+\s+)*?(?:View|ViewModifier|ToolbarContent)\b"
)

# Capitalized type used as a constructor (optional single-level generic args).
SWIFT_TYPE_CONSTRUCTOR = r"[A-Z][\w]*(?:<[^>]+>)?"

DOT_INIT_WRAPPED_VALUE_RE = re.compile(
    rf"\b(?P<wrapper>StateObject|State|ObservedObject|Binding)\(wrappedValue:\s*"
    rf"(?P<ctor>(?!\.init\b){SWIFT_TYPE_CONSTRUCTOR})\s*\("
)

DOT_INIT_TYPED_ASSIGN_RE = re.compile(
    rf"(?P<prefix>:\s*(?P<type>{SWIFT_TYPE_CONSTRUCTOR})\s*=\s*)"
    rf"(?P<ctor>(?!\.init\b){SWIFT_TYPE_CONSTRUCTOR})\s*\("
)


@dataclass
class StringLiteralState:
    """Whether a scan is inside ``\"\"\"``, ``\"…\"``, or a raw ``#\"…\"#`` literal."""

    in_triple: bool = False
    in_string: bool = False
    escape: bool = False
    # 0 = ordinary string; >0 = raw string opened with that many ``#``.
    raw_hash_count: int = 0

    @property
    def in_literal(self) -> bool:
        return self.in_triple or self.in_string


def _raw_hashes_at(text: str, i: int) -> int:
    n = 0
    while i + n < len(text) and text[i + n] == "#":
        n += 1
    return n


def _try_open_string(text: str, i: int) -> tuple[StringLiteralState, int] | None:
    """If ``text[i:]`` opens a string literal, return (state, index after opener)."""
    hashes = _raw_hashes_at(text, i)
    j = i + hashes
    if text.startswith('"""', j):
        return StringLiteralState(True, False, False, hashes), j + 3
    if j < len(text) and text[j] == '"':
        return StringLiteralState(False, True, False, hashes), j + 1
    return None


def _try_close_string(state: StringLiteralState, text: str, i: int) -> int | None:
    """If ``text[i:]`` closes ``state``'s literal, return index after closer."""
    if state.in_triple:
        if not text.startswith('"""', i):
            return None
        j = i + 3
        if state.raw_hash_count:
            if text[j : j + state.raw_hash_count] != "#" * state.raw_hash_count:
                return None
            return j + state.raw_hash_count
        return j
    if state.in_string:
        if state.raw_hash_count:
            if text[i] != '"':
                return None
            j = i + 1
            if text[j : j + state.raw_hash_count] != "#" * state.raw_hash_count:
                return None
            return j + state.raw_hash_count
        if state.escape:
            return None
        if text[i] == '"':
            return i + 1
    return None


def advance_string_literal_state(
    state: StringLiteralState, text: str, start: int = 0, end: int | None = None
) -> StringLiteralState:
    """Return ``state`` after scanning ``text[start:end]`` for string literals."""
    if end is None:
        end = len(text)
    i = start
    cur = StringLiteralState(
        state.in_triple, state.in_string, state.escape, state.raw_hash_count
    )
    while i < end:
        if cur.in_literal:
            closer = _try_close_string(cur, text, i)
            if closer is not None and closer <= end:
                cur = StringLiteralState()
                i = closer
                continue
            if cur.in_string and not cur.raw_hash_count:
                ch = text[i]
                if cur.escape:
                    cur = StringLiteralState(False, True, False, 0)
                elif ch == "\\":
                    cur = StringLiteralState(False, True, True, 0)
                i += 1
                continue
            i += 1
            continue
        opened = _try_open_string(text, i)
        if opened is not None:
            cur, i = opened
            continue
        i += 1
    return cur


def brace_delta_outside_strings(
    text: str, state: StringLiteralState | None = None
) -> tuple[int, StringLiteralState]:
    """Net ``{``/``}`` delta in ``text``, ignoring string literal contents."""
    if state is None:
        state = StringLiteralState()
    cur = StringLiteralState(
        state.in_triple, state.in_string, state.escape, state.raw_hash_count
    )
    delta = 0
    i = 0
    n = len(text)
    while i < n:
        if cur.in_literal:
            closer = _try_close_string(cur, text, i)
            if closer is not None:
                cur = StringLiteralState()
                i = closer
                continue
            if cur.in_string and not cur.raw_hash_count:
                ch = text[i]
                if cur.escape:
                    cur = StringLiteralState(False, True, False, 0)
                elif ch == "\\":
                    cur = StringLiteralState(False, True, True, 0)
                i += 1
                continue
            i += 1
            continue
        opened = _try_open_string(text, i)
        if opened is not None:
            cur, i = opened
            continue
        ch = text[i]
        if ch == "{":
            delta += 1
        elif ch == "}":
            delta -= 1
        i += 1
    return delta, cur


def has_brace_outside_strings(
    text: str, state: StringLiteralState | None = None
) -> bool:
    if state is None:
        state = StringLiteralState()
    cur = StringLiteralState(
        state.in_triple, state.in_string, state.escape, state.raw_hash_count
    )
    i = 0
    n = len(text)
    while i < n:
        if cur.in_literal:
            closer = _try_close_string(cur, text, i)
            if closer is not None:
                cur = StringLiteralState()
                i = closer
                continue
            if cur.in_string and not cur.raw_hash_count:
                ch = text[i]
                if cur.escape:
                    cur = StringLiteralState(False, True, False, 0)
                elif ch == "\\":
                    cur = StringLiteralState(False, True, True, 0)
                i += 1
                continue
            i += 1
            continue
        opened = _try_open_string(text, i)
        if opened is not None:
            cur, i = opened
            continue
        if text[i] in "{}":
            return True
        i += 1
    return False


def find_next_brace_outside_strings(text: str, start: int) -> int | None:
    """Return index of the next ``{`` at or after ``start``, ignoring strings."""
    state = StringLiteralState()
    i = start
    n = len(text)
    while i < n:
        if state.in_literal:
            closer = _try_close_string(state, text, i)
            if closer is not None:
                state = StringLiteralState()
                i = closer
                continue
            if state.in_string and not state.raw_hash_count:
                ch = text[i]
                if state.escape:
                    state = StringLiteralState(False, True, False, 0)
                elif ch == "\\":
                    state = StringLiteralState(False, True, True, 0)
                i += 1
                continue
            i += 1
            continue
        opened = _try_open_string(text, i)
        if opened is not None:
            state, i = opened
            continue
        if text[i] == "{":
            return i
        i += 1
    return None


def find_matching_close_brace(text: str, open_index: int) -> int | None:
    """Return index of ``}`` matching ``text[open_index] == '{'``, string-aware."""
    if open_index < 0 or open_index >= len(text) or text[open_index] != "{":
        return None
    depth = 0
    state = advance_string_literal_state(StringLiteralState(), text, 0, open_index)
    i = open_index
    n = len(text)
    while i < n:
        if state.in_literal:
            closer = _try_close_string(state, text, i)
            if closer is not None:
                state = StringLiteralState()
                i = closer
                continue
            if state.in_string and not state.raw_hash_count:
                ch = text[i]
                if state.escape:
                    state = StringLiteralState(False, True, False, 0)
                elif ch == "\\":
                    state = StringLiteralState(False, True, True, 0)
                i += 1
                continue
            i += 1
            continue
        opened = _try_open_string(text, i)
        if opened is not None:
            state, i = opened
            continue
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


TYPE_DECL_MODIFIERS = (
    r"(?:@\w+\s+|private |fileprivate |public |package |open |final |@MainActor )*"
)

TYPE_DECL_RE = re.compile(
    rf"^({TYPE_DECL_MODIFIERS})(struct|class|enum|actor)\s+(\w+)",
    re.MULTILINE,
)

# Top-level type or ``extension`` (name may be ``Color``, ``[AppRoute]``, …).
TYPE_OR_EXTENSION_DECL_RE = re.compile(
    rf"^({TYPE_DECL_MODIFIERS})(?:(struct|class|enum|actor|protocol)\s+(\w+)|(extension)\b)",
    re.MULTILINE,
)

# Swift identifiers, including reserved names written as `Type`.
SWIFT_NAME = r"(?:`[^`]+`|\w+)"

# ``init``, ``init?``, ``init!``, with ACL / required / convenience / override.
INIT_DECL_RE = re.compile(
    r"^(?:(?:private|fileprivate|public|package|open|required|convenience|"
    r"override|final)\s+)*init[?!]?\s*\("
)

TYPE_MEMBER_LINE_RE = re.compile(
    r"^(?:(?:private|fileprivate|public|package|open)\s+)*"
    r"(?:final\s+)?"
    rf"(?:typealias\s+{SWIFT_NAME}\s*=|associatedtype\s+{SWIFT_NAME}\b|(?:struct|class|enum)\s+{SWIFT_NAME}(?:\s*[:\{{<]|$))",
)


def acl_of(text: str) -> str:
    # Use the declaration line, not a fixed head window: a long ``///`` can
    # push ``private func`` past line 6 (or a comment can mention "private").
    head = first_code_line(text)
    # Rank by read access: drop ``private(set)`` / ``fileprivate(set)`` so
    # ``public private(set)`` stays public and bare ``private(set)`` is internal.
    head = re.sub(r"\b(?:private|fileprivate)\s*\(\s*set\s*\)\s*", "", head)
    for acl in ("private", "fileprivate", "public", "package", "open"):
        if re.search(rf"\b{acl}\b", head):
            if acl == "open":
                return "public"
            return acl
    return "internal"


def member_name(text: str) -> str:
    stripped = first_code_line(text)
    m = re.search(rf"\b(?:typealias|associatedtype|struct|class|enum)\s+({SWIFT_NAME})", stripped)
    if m:
        return m.group(1).strip("`").lower()
    if INIT_DECL_RE.match(stripped):
        return "init"
    m = re.search(r"\bfunc\s+(\w+)", stripped)
    if m:
        return m.group(1).lower()
    m = re.search(r"\b(?:var|let)\s+(\w+)", stripped)
    if m:
        return m.group(1).lower()
    m = re.search(r"\bcase\s+(\w+)", stripped)
    if m:
        return m.group(1).lower()
    return stripped[:24].lower()


_DECL_KEYWORDS_RE = re.compile(
    r"\b(?:var|let|func|init|deinit|struct|class|enum|actor|typealias|associatedtype)\b"
)


def is_attribute_only_line(stripped: str) -> bool:
    """True for a lone ``@Foo`` / ``@Foo(...)`` line, not ``@ViewBuilder var …``."""
    if not stripped.startswith("@"):
        return False
    if stripped.startswith("@Preview"):
        return False
    return not _DECL_KEYWORDS_RE.search(stripped)


def first_code_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("//") or stripped.startswith("#"):
            continue
        # Attribute-only lines (e.g. ``@ViewBuilder`` on its own line) are not the member.
        if is_attribute_only_line(stripped):
            continue
        if is_import_line(stripped):
            continue
        return stripped
    return text.strip()


def file_private_head(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("//") or stripped.startswith("#"):
            continue
        if stripped.startswith("@"):
            continue
        if is_import_line(stripped):
            continue
        return stripped
    return text.strip()


def is_type_member_line(stripped: str) -> bool:
    return bool(TYPE_MEMBER_LINE_RE.match(stripped))


def advance_angle_paren(angle: int, paren: int, line: str) -> tuple[int, int]:
    """Track ``<``/``>`` and ``(``/``)`` depth, ignoring ``->`` return-type arrows."""
    for i, ch in enumerate(line):
        if ch == "<":
            angle += 1
        elif ch == ">" and (i == 0 or line[i - 1] != "-"):
            angle -= 1
        elif ch == "(":
            paren += 1
        elif ch == ")":
            paren -= 1
    return angle, paren


def is_typealias_member(text: str) -> bool:
    """``typealias`` and ``associatedtype`` are type members, not stored vars."""
    return bool(
        re.search(
            rf"\b(?:typealias|associatedtype)\s+{SWIFT_NAME}\b",
            first_code_line(text),
        )
    )


def is_subscript_member(text: str) -> bool:
    """A function written without ``func``, such as ``subscript``."""
    return bool(re.search(r"\bsubscript\b", first_code_line(text)))


def member_kind(text: str) -> str:
    stripped = first_code_line(text)
    if is_type_member_line(stripped) or is_typealias_member(text):
        return "type"
    if re.search(r"\bstatic\b", stripped):
        if re.search(r"\bfunc\b", stripped):
            return "static_fn"
        if re.search(r"\b(?:var|let)\b", stripped) or re.search(r"\bsubscript\b", stripped):
            return "static_var"
    if INIT_DECL_RE.match(stripped):
        return "instance_fn"
    if re.search(r"\bdeinit\b", stripped):
        return "instance_fn"
    if re.search(r"\bfunc\b", stripped):
        return "instance_fn"
    if re.search(r"\b(?:var|let)\b", stripped):
        return "instance_var"
    return "instance_var"


def is_body_member(text: str) -> bool:
    stripped = text.strip()
    return bool(
        re.match(
            r"^(?:(?:@\w+(?:\([^)]*\))?\s*)|@ViewBuilder\s+)*var\s+body\s*:",
            stripped,
            re.DOTALL,
        )
    )


def is_enum_case_member(text: str) -> bool:
    stripped = first_code_line(text)
    return stripped.startswith("case ") or stripped.startswith("indirect case ")


def is_private_member(text: str) -> bool:
    stripped = first_code_line(text)
    # ``private(set)`` / ``fileprivate(set)`` keep internal read access.
    if re.search(r"\b(?:private|fileprivate)\s*\(\s*set\s*\)", stripped):
        return False
    return acl_of(text) in ("private", "fileprivate")


def is_computed_instance_var(text: str) -> bool:
    """True for ``var name: T { … }``, including getters that bind ``let``.

    Only the declaration line is considered. A stored ``let`` / ``var name =``
    (with or without observers) is not computed. A ``let`` inside the getter
    body must not flip a computed property into the exposed-stored bucket —
    that hoisted ``urlPath`` ahead of ``httpHeaderFields``.
    """
    stripped = first_code_line(text)
    if not re.search(rf"\bvar\s+{SWIFT_NAME}", stripped):
        return False
    before_brace = stripped.split("{", 1)[0]
    if "=" in before_brace:
        return False
    return "{" in text


def is_exposed_stored_property(text: str) -> bool:
    if member_kind(text) != "instance_var":
        return False
    if is_subscript_member(text):
        return False
    if is_private_member(text):
        return False
    if is_computed_instance_var(text):
        return False
    return True


def is_deinit_member(text: str) -> bool:
    return bool(re.search(r"\bdeinit\b", first_code_line(text)))


def is_init_member(text: str) -> bool:
    if member_kind(text) != "instance_fn":
        return False
    if is_deinit_member(text):
        return False
    return bool(INIT_DECL_RE.match(first_code_line(text)))


def sort_members(members: list[str], kinds: tuple[str, ...]) -> list[str]:
    filtered = [m for m in members if member_kind(m) in kinds]
    filtered.sort(
        key=lambda m: (
            ACL_RANK[acl_of(m)],
            VAR_BEFORE_FN_RANK.get(member_kind(m), 0),
            0 if is_subscript_member(m) else 1,
            member_name(m),
        )
    )
    return filtered


def sort_before_body_members(members: list[str]) -> list[str]:
    """Types, then static vars, then static funcs.

    Within each kind, sort by ACL, then name. Kind outranks ACL, so a private
    nested type stays ahead of an internal static var.
    """
    filtered = [m for m in members if member_kind(m) in BEFORE_BODY_KINDS]
    filtered.sort(
        key=lambda m: (
            BEFORE_BODY_MEMBER_RANK[member_kind(m)],
            ACL_RANK[acl_of(m)],
            0 if is_subscript_member(m) else 1,
            member_name(m),
        )
    )
    return filtered


def _merge_exposed_stored_by_name(members: list[str]) -> list[str]:
    """Within one ACL: keep exposed stored order; insert other vars by name."""
    exposed = [m for m in members if is_exposed_stored_property(m)]
    other = sort_members(
        [m for m in members if m not in exposed],
        ("instance_var",),
    )
    result: list[str] = []
    other_i = 0
    for stored in exposed:
        stored_name = member_name(stored)
        while other_i < len(other) and member_name(other[other_i]) < stored_name:
            result.append(other[other_i])
            other_i += 1
        result.append(stored)
    result.extend(other[other_i:])
    return result


def sort_internal_vars(members: list[str]) -> list[str]:
    """Public vars, then package, then internal; merge by name within each ACL.

    ACL outranks name so an internal ``pathComponent`` stays after public
    ``httpMethod`` / ``request``. Within an ACL, putting every exposed stored
    property first wrongly hoists later stored names (``downloadURL``) ahead of
    alphabetically earlier computed ones (``containsGitLFSPointer``) — merge
    non-stored members into the stored sequence by name instead.
    """
    # ``open`` maps to ``public`` in ``acl_of``; private/fileprivate are
    # filtered out before this runs.
    by_acl: dict[str, list[str]] = {"public": [], "package": [], "internal": []}
    for member in members:
        by_acl[acl_of(member)].append(member)

    result: list[str] = []
    for acl in ("public", "package", "internal"):
        group = by_acl[acl]
        if not group:
            continue
        subscripts = [m for m in group if is_subscript_member(m)]
        rest = [m for m in group if not is_subscript_member(m)]
        subscripts.sort(key=member_name)
        result.extend(subscripts)
        result.extend(_merge_exposed_stored_by_name(rest))
    return result


def sort_after_body_members(members: list[str]) -> list[str]:
    internal_vars: list[str] = []
    internal_fns: list[str] = []
    inits: list[str] = []
    deinits: list[str] = []
    private_vars: list[str] = []
    private_fns: list[str] = []
    leftover: list[str] = []

    for member in members:
        if is_init_member(member):
            inits.append(member)
        elif is_deinit_member(member):
            deinits.append(member)
        elif member_kind(member) == "instance_fn":
            if is_private_member(member):
                private_fns.append(member)
            else:
                internal_fns.append(member)
        elif member_kind(member) == "instance_var":
            if is_private_member(member):
                private_vars.append(member)
            else:
                internal_vars.append(member)
        else:
            leftover.append(member)

    return (
        sort_internal_vars(internal_vars)
        + sort_members(private_vars, ("instance_var",))
        + sort_members(internal_fns, ("instance_fn",))
        + sort_members(inits, ("instance_fn",))
        + sort_members(deinits, ("instance_fn",))
        + sort_members(private_fns, ("instance_fn",))
        + leftover
    )


DEBUG_IF_RE = re.compile(r"^#if\s+DEBUG(?:\s*//.*)?$")


def is_debug_only_if(stripped: str) -> bool:
    """True for ``#if DEBUG`` (optional trailing ``//`` comment)."""
    return bool(DEBUG_IF_RE.match(stripped))


def _directive_outside_strings(line: str, state: StringLiteralState) -> str | None:
    """Stripped ``#…`` directive when ``line`` is not inside a string literal."""
    if state.in_literal:
        return None
    stripped = line.strip()
    if stripped.startswith("#"):
        return stripped
    return None


def if_block_has_else_or_elseif(block_lines: list[str]) -> bool:
    """True when a top-level ``#else`` / ``#elseif`` appears in the ``#if`` block.

    Directives inside string literals do not count.
    """
    depth = 0
    state = StringLiteralState()
    for line in block_lines:
        directive = _directive_outside_strings(line, state)
        if directive:
            if directive.startswith("#if"):
                depth += 1
            elif directive.startswith("#endif"):
                depth -= 1
            elif depth == 1 and (
                directive == "#else"
                or directive.startswith("#else ")
                or directive.startswith("#elseif")
                or directive.startswith("#elif")
            ):
                return True
        state = advance_string_literal_state(state, line)
    return False


def wrap_member_in_debug_if(member: str) -> str:
    """Wrap a member in ``#if DEBUG`` / ``#endif`` (column-0 directives)."""
    body = member.rstrip("\n")
    if not body.strip():
        return body
    return f"#if DEBUG\n{body}\n#endif"


def collect_if_block(lines: list[str], start: int) -> tuple[int, list[str]]:
    """Return ``(end_exclusive, block_lines)`` for the ``#if`` at ``start``.

    ``#if`` / ``#endif`` inside string literals do not change depth.
    """
    depth = 0
    i = start
    block: list[str] = []
    n = len(lines)
    state = StringLiteralState()
    while i < n:
        line = lines[i]
        block.append(line)
        directive = _directive_outside_strings(line, state)
        state = advance_string_literal_state(state, line)
        i += 1
        if not directive:
            continue
        if directive.startswith("#if"):
            depth += 1
        elif directive.startswith("#endif"):
            depth -= 1
            if depth == 0:
                return i, block
    return i, block


def split_debug_if_block(block_lines: list[str], prefix: list[str]) -> list[str]:
    """Split a pure ``#if DEBUG`` block into per-member wrapped chunks.

    Members are sorted later as if the directive were absent; each keeps its own
    ``#if DEBUG`` / ``#endif``. Mixed blocks (imports plus types / helpers)
    stay one wrapper — splitting them into an import-only preamble and a
    second ``#if DEBUG`` is the opposite of DEBUG coalescing. Import-only
    blocks stay one chunk so file-layout can keep them in the preamble.
    Blocks with ``#else`` / ``#elseif`` stay opaque.
    """
    if len(block_lines) < 2 or if_block_has_else_or_elseif(block_lines):
        return [("".join(prefix + block_lines)).rstrip("\n")]

    interior = "".join(block_lines[1:-1])
    inner_members = split_top_level_members(interior)
    if not inner_members:
        return [("".join(prefix + block_lines)).rstrip("\n")]

    imports = [m for m in inner_members if is_import_only_chunk(m)]
    rest = [m for m in inner_members if not is_import_only_chunk(m)]

    # Imports + declarations: one wrapper, imports first.
    if imports and rest:
        ordered = "\n\n".join(m.strip("\n") for m in imports + rest)
        block = f"#if DEBUG\n{ordered}\n#endif"
        if prefix:
            block = ("".join(prefix) + block).rstrip("\n")
        return [block]

    ordered_inners = imports + rest
    out: list[str] = []
    for index, inner in enumerate(ordered_inners):
        piece = inner
        if index == 0 and prefix:
            piece = ("".join(prefix) + piece).rstrip("\n")
        out.append(wrap_member_in_debug_if(piece))
    return out


def split_top_level_members(body: str) -> list[str]:
    lines = body.splitlines(keepends=True)
    members: list[str] = []
    i = 0
    n = len(lines)

    def skip_blank(pos: int) -> int:
        while pos < n and lines[pos].strip() == "":
            pos += 1
        return pos

    while i < n:
        i = skip_blank(i)
        if i >= n:
            break

        prefix: list[str] = []
        consumed_debug = False
        while i < n:
            line = lines[i]
            stripped = line.strip()
            if is_debug_only_if(stripped):
                # Split into members, sort later as bare decls, re-wrap each.
                i, block_lines = collect_if_block(lines, i)
                members.extend(split_debug_if_block(block_lines, prefix))
                prefix = []
                consumed_debug = True
                break
            if stripped.startswith("#if"):
                # Non-DEBUG conditionals stay opaque and attach to the next member.
                i, block_lines = collect_if_block(lines, i)
                prefix.extend(block_lines)
                continue
            if stripped.startswith("#") and not is_preview_line(stripped):
                prefix.append(line)
                i += 1
                continue
            if is_attribute_only_line(stripped):
                prefix.append(line)
                i += 1
                continue
            if stripped.startswith("///") or stripped.startswith("//"):
                prefix.append(line)
                i += 1
                continue
            break

        if consumed_debug:
            continue

        if i >= n:
            if prefix:
                members.append("".join(prefix).rstrip("\n"))
            break

        start = i
        line = lines[i]
        stripped = line.strip()

        if is_preview_line(stripped):
            depth = 0
            started = False
            scan = StringLiteralState()
            while i < n:
                delta, scan = brace_delta_outside_strings(lines[i], scan)
                if not started and delta != 0:
                    started = True
                depth += delta
                i += 1
                if started and depth == 0 and not scan.in_literal:
                    break
            chunk = normalize_preview_chunk(
                "".join(prefix + lines[start:i]).rstrip("\n")
            )
            members.append(chunk)
            continue

        if is_type_member_line(stripped):
            if not has_brace_outside_strings(stripped):
                i = start
                angle = 0
                paren = 0
                brace_depth = 0
                body_started = False
                scan = StringLiteralState()
                while i < n:
                    if not body_started:
                        angle, paren = advance_angle_paren(angle, paren, lines[i])
                        if (
                            has_brace_outside_strings(lines[i], scan)
                            and angle == 0
                            and paren == 0
                        ):
                            body_started = True
                            brace_depth, scan = brace_delta_outside_strings(
                                lines[i], scan
                            )
                        else:
                            scan = advance_string_literal_state(scan, lines[i])
                    else:
                        delta, scan = brace_delta_outside_strings(lines[i], scan)
                        brace_depth += delta
                    i += 1
                    if body_started and brace_depth == 0 and not scan.in_literal:
                        break
                    if (
                        not body_started
                        and is_typealias_member(stripped)
                        and paren == 0
                        and angle == 0
                        and i > start
                    ):
                        break
                if not body_started and not (
                    is_typealias_member(stripped)
                    and paren == 0
                    and angle == 0
                    and i > start
                ):
                    i = start + 1
            else:
                depth = 0
                started = False
                scan = StringLiteralState()
                while i < n:
                    delta, scan = brace_delta_outside_strings(lines[i], scan)
                    if not started and delta != 0:
                        started = True
                    depth += delta
                    i += 1
                    if started and depth == 0 and not scan.in_literal:
                        break
            chunk = "".join(prefix + lines[start:i])
            members.append(chunk.rstrip("\n"))
            continue

        if (
            has_brace_outside_strings(line)
            and re.search(r"\b(?:var|let|func|init)\b", line)
            and not stripped.endswith("{")
        ):
            depth = 0
            scan = StringLiteralState()
            while i < n:
                delta, scan = brace_delta_outside_strings(lines[i], scan)
                depth += delta
                i += 1
                if depth == 0 and not scan.in_literal:
                    break
            chunk = "".join(prefix + lines[start:i])
            members.append(chunk.rstrip("\n"))
            continue

        if (
            re.search(r"\bfunc\b", stripped)
            or re.search(r"\bdeinit\b", stripped)
            or re.match(
                r"^(?:(?:private|fileprivate|public|package|open)\s+)*init\s*\(",
                stripped,
            )
            or re.match(r"^init\s*\(", stripped)
        ):
            paren_depth = 0
            brace_started = False
            depth = 0
            saw_paren = False
            scan = StringLiteralState()
            while i < n:
                line = lines[i]
                if not brace_started:
                    j = 0
                    while j < len(line):
                        if scan.in_literal:
                            closer = _try_close_string(scan, line, j)
                            if closer is not None:
                                scan = StringLiteralState()
                                j = closer
                                continue
                            if scan.in_string and not scan.raw_hash_count:
                                ch = line[j]
                                if scan.escape:
                                    scan = StringLiteralState(False, True, False, 0)
                                elif ch == "\\":
                                    scan = StringLiteralState(False, True, True, 0)
                                j += 1
                                continue
                            j += 1
                            continue
                        opened = _try_open_string(line, j)
                        if opened is not None:
                            scan, j = opened
                            continue
                        ch = line[j]
                        if ch == "(":
                            paren_depth += 1
                            saw_paren = True
                        elif ch == ")":
                            paren_depth -= 1
                        elif ch == "{" and paren_depth == 0:
                            brace_started = True
                            depth = 1
                        elif ch == "}" and brace_started:
                            depth -= 1
                        j += 1
                else:
                    delta, scan = brace_delta_outside_strings(line, scan)
                    depth += delta
                i += 1
                if brace_started and depth == 0 and not scan.in_literal:
                    break
                # Protocol / forward declarations: signature ends when parens
                # balance with no body brace (do not absorb following members).
                # Keep going when the next line continues the signature
                # (``->``, ``where``, ``async`` / ``throws``, or ``{``).
                if not brace_started and saw_paren and paren_depth == 0:
                    j = i
                    while j < n and lines[j].strip() == "":
                        j += 1
                    if j >= n:
                        break
                    nxt = lines[j].strip()
                    if (
                        nxt.startswith("->")
                        or nxt.startswith("where")
                        or nxt.startswith("async")
                        or nxt.startswith("throws")
                        or nxt.startswith("rethrows")
                        or nxt.startswith("{")
                    ):
                        continue
                    break
            chunk = "".join(prefix + lines[start:i])
            members.append(chunk.rstrip("\n"))
            continue

        if stripped.endswith("{") and has_brace_outside_strings(stripped):
            depth = 0
            scan = StringLiteralState()
            while i < n:
                delta, scan = brace_delta_outside_strings(lines[i], scan)
                depth += delta
                i += 1
                if depth == 0 and not scan.in_literal:
                    break
            chunk = "".join(prefix + lines[start:i])
            members.append(chunk.rstrip("\n"))
            continue

        end = i + 1
        scan = advance_string_literal_state(StringLiteralState(), lines[i])
        while end < n:
            if scan.in_literal:
                scan = advance_string_literal_state(scan, lines[end])
                end += 1
                continue
            nxt = lines[end].strip()
            if nxt == "":
                break
            if nxt.startswith("#") or nxt.startswith("@") or nxt.startswith("///"):
                break
            if is_type_member_line(nxt):
                break
            if re.match(
                rf"^(?:(?:private|fileprivate|public|package|open)\s+)?(?:@|{SWIFT_NAME})",
                nxt,
            ) and re.search(r"\b(?:var|let|func|init|deinit|static|case)\b", nxt):
                break
            scan = advance_string_literal_state(scan, lines[end])
            end += 1
        chunk = "".join(prefix + lines[start:end])
        members.append(chunk.rstrip("\n"))
        i = end

    cleaned: list[str] = []
    for member in members:
        if member.strip().startswith("#endif"):
            if cleaned:
                cleaned[-1] = cleaned[-1] + "\n\n" + member
            else:
                cleaned.append(member)
            continue
        cleaned.append(member)
    return [m for m in cleaned if m.strip()]


def join_members(parts: list[str]) -> str:
    filtered = [p for p in parts if p.strip()]
    if not filtered:
        return ""
    return "\n\n".join(filtered) + "\n"


def _leading_indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def member_indent(member: str) -> str:
    for line in member.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("#"):
            continue
        if is_attribute_only_line(stripped):
            continue
        return _leading_indent(line)
    return "  "


def _split_top_level_commas(text: str) -> list[str]:
    items: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(text):
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth -= 1
        elif ch == "," and depth == 0:
            item = text[start:i].strip()
            if item:
                items.append(item)
            start = i + 1
    tail = text[start:].strip()
    if tail:
        items.append(tail)
    return items


def _item_has_associated_value(item: str) -> bool:
    depth = 0
    for ch in item:
        if ch == "(" and depth == 0:
            return True
        if ch == "=" and depth == 0:
            return False
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth -= 1
    return False


def _item_has_raw_value(item: str) -> bool:
    depth = 0
    for ch in item:
        if ch == "=" and depth == 0:
            return True
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth -= 1
    return False


def _strip_trailing_comment(line: str) -> tuple[str, bool]:
    """Return the code and whether a ``//`` comment was stripped."""
    depth = 0
    in_str = None
    i = 0
    while i < len(line):
        ch = line[i]
        if in_str:
            if ch == "\\" and i + 1 < len(line):
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in ("'", '"'):
            in_str = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "/" and i + 1 < len(line) and line[i + 1] == "/" and depth == 0:
            return line[:i].rstrip(), True
        i += 1
    return line.rstrip(), False


def parse_enum_case_items(code_line: str) -> tuple[str, list[str]] | None:
    code, _had_comment = _strip_trailing_comment(code_line)
    match = re.match(r"^(?:indirect\s+)?case\s+(.+)$", code.strip())
    if not match:
        return None
    items = _split_top_level_commas(match.group(1))
    if not items:
        return None
    if any(_item_has_associated_value(item) for item in items):
        return ("associated", items)
    if any(_item_has_raw_value(item) for item in items):
        return ("raw", items)
    return ("simple", items)


def case_member_has_own_comment(member: str) -> bool:
    for line in member.splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            return True
    _code, had_comment = _strip_trailing_comment(first_code_line(member))
    return had_comment


def case_member_kind(member: str) -> str:
    if not is_enum_case_member(member):
        return "not_case"
    if case_member_has_own_comment(member):
        return "solo"
    parsed = parse_enum_case_items(first_code_line(member))
    if parsed is None or parsed[0] in ("associated", "raw"):
        return "solo"
    return "mergeable"


def format_simple_case_line(indent: str, items: list[str]) -> str:
    if not items:
        return ""
    prefix = f"{indent}case "
    # Align with the first name so SwiftFormat ``indent`` is happy.
    continuation = " " * len(prefix)
    lines: list[str] = []
    current = prefix
    first = True
    for index, item in enumerate(items):
        last = index == len(items) - 1
        piece = item if first else f", {item}"
        candidate = current + piece
        # Leave room for a trailing comma when this line may continue.
        limit = ENUM_CASE_LINE_LENGTH if last else ENUM_CASE_LINE_LENGTH - 1
        if not first and len(candidate) > limit:
            lines.append(current.rstrip() + ",")
            current = continuation + item
        else:
            current = candidate
        first = False
    lines.append(current.rstrip())
    return "\n".join(lines)


def case_items_from_member(member: str) -> list[str]:
    """Case payloads from a member, including wrap continuations."""
    items: list[str] = []
    seen_case = False
    for line in member.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith("#"):
            continue
        if is_attribute_only_line(stripped):
            continue
        code, _had_comment = _strip_trailing_comment(stripped)
        if not seen_case:
            parsed = parse_enum_case_items(code)
            if parsed is None or parsed[0] == "associated":
                return []
            items.extend(parsed[1])
            seen_case = True
            continue
        if code.startswith("case ") or code.startswith("indirect case "):
            parsed = parse_enum_case_items(code)
            if parsed is not None and parsed[0] != "associated":
                items.extend(parsed[1])
            continue
        items.extend(_split_top_level_commas(code))
    return items


def format_case_run(members: list[str]) -> list[str]:
    formatted: list[str] = []
    pending: list[str] = []
    pending_indent = "  "

    def flush() -> None:
        if pending:
            formatted.append(format_simple_case_line(pending_indent, pending))
            pending.clear()

    for member in members:
        if case_member_has_own_comment(member):
            flush()
            formatted.append(member.rstrip("\n"))
            continue
        parsed = parse_enum_case_items(first_code_line(member))
        if parsed is None or parsed[0] == "associated":
            flush()
            formatted.append(member.rstrip("\n"))
            continue
        items = case_items_from_member(member) or parsed[1]
        indent = member_indent(member)
        for item in items:
            if _item_has_raw_value(item) or _item_has_associated_value(item):
                flush()
                formatted.append(f"{indent}case {item}")
                continue
            if not pending:
                pending_indent = indent
            pending.append(item)
    flush()
    return formatted


def _member_span_in_body(body: str, member: str, start: int) -> tuple[int, int] | None:
    idx = body.find(member, start)
    if idx != -1:
        return idx, idx + len(member)
    stripped = member.strip()
    idx = body.find(stripped, start)
    if idx == -1:
        return None
    return idx, idx + len(stripped)


def format_enum_body_cases(body: str) -> str:
    members = split_top_level_members(body)
    if not members:
        return body

    pos = 0
    spans: list[tuple[int, int]] = []
    for member in members:
        span = _member_span_in_body(body, member, pos)
        if span is None:
            return body
        spans.append(span)
        pos = span[1]

    replacements: list[tuple[int, int, str]] = []
    i = 0
    while i < len(members):
        if not is_enum_case_member(members[i]):
            i += 1
            continue
        run_start = i
        run: list[str] = []
        while i < len(members) and is_enum_case_member(members[i]):
            run.append(members[i])
            i += 1
        formatted = format_case_run(run)
        start = spans[run_start][0]
        end = spans[i - 1][1]
        replacement = "\n\n".join(formatted)
        if replacement == body[start:end]:
            continue
        replacements.append((start, end, replacement))

    if not replacements:
        return body
    new_body = body
    for start, end, text in reversed(replacements):
        new_body = new_body[:start] + text + new_body[end:]
    return new_body


ENUM_DECL_RE = re.compile(
    rf"^([ \t]*)({TYPE_DECL_MODIFIERS})enum\s+(\w+)",
    re.MULTILINE,
)


def find_enum_body_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in ENUM_DECL_RE.finditer(text):
        brace_pos = find_next_brace_outside_strings(text, match.end())
        if brace_pos is None:
            continue
        close = find_matching_close_brace(text, brace_pos)
        if close is None:
            continue
        spans.append((brace_pos + 1, close))
    return spans


def format_enum_cases_in_text(text: str) -> str:
    for start, end in reversed(find_enum_body_spans(text)):
        body = text[start:end]
        new_body = format_enum_body_cases(body)
        if new_body == body:
            continue
        if (new_body.count("{"), new_body.count("}")) != (
            body.count("{"),
            body.count("}"),
        ):
            continue
        text = text[:start] + new_body + text[end:]
    return text


def is_import_line(stripped: str) -> bool:
    return stripped.startswith("import ") or stripped.startswith("@testable import ")


def is_conditional_compilation_line(stripped: str) -> bool:
    return (
        stripped.startswith("#if")
        or stripped.startswith("#endif")
        or stripped.startswith("#else")
        or stripped.startswith("#elseif")
        or stripped.startswith("#elif")
    )


def is_import_only_chunk(text: str) -> bool:
    """True when every code line is ``import`` / ``@testable import``.

    ``#if DEBUG`` / ``#endif`` wrappers and comments are ignored so a
    file-scope DEBUG import stays in the import preamble instead of being
    ranked as leftover and moved below types.
    """
    saw_import = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("//") or is_conditional_compilation_line(stripped):
            continue
        if is_import_line(stripped):
            saw_import = True
            continue
        return False
    return saw_import


def split_imports(text: str) -> tuple[str, str]:
    lines = text.splitlines(keepends=True)
    i = 0
    n = len(lines)
    while i < n:
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        if is_import_line(stripped):
            i += 1
            continue
        if stripped.startswith("#if"):
            end, block = collect_if_block(lines, i)
            if is_import_only_chunk("".join(block)):
                # Leave ``#if DEBUG`` import-only when the next chunk is also
                # pure DEBUG so file-layout can coalesce into one wrapper.
                if is_debug_only_if(stripped):
                    j = end
                    while j < n and lines[j].strip() == "":
                        j += 1
                    if j < n and is_debug_only_if(lines[j].strip()):
                        break
                i = end
                continue
        break
    return "".join(lines[:i]), "".join(lines[i:])


def is_private_fileprivate_chunk(text: str) -> bool:
    return bool(
        re.match(r"^(?:private|fileprivate)\s+", file_private_head(text))
    )


def is_extension_chunk(text: str) -> bool:
    """True for ``extension`` and ``private`` / ``fileprivate extension``."""
    head = first_code_line(text)
    return bool(
        re.match(r"^(?:(?:private|fileprivate)\s+)?extension\b", head)
    )


def is_type_declaration_chunk(text: str) -> bool:
    head = first_code_line(text)
    if head.startswith("extension "):
        return False
    return bool(
        re.search(r"\b(struct|class|enum|actor|protocol)\s+", head)
    )


def is_preview_line(stripped: str) -> bool:
    return stripped.startswith("#Preview") or stripped.startswith("@Preview")


def is_preview_chunk(text: str) -> bool:
    """True when the chunk's first code line is ``#Preview`` / ``@Preview``.

    ``first_code_line`` skips every ``#`` line, which would also skip
    ``#Preview``. Comments, attributes, and ``#if`` / ``#endif`` wrappers
    are ignored so a DEBUG-wrapped preview still counts.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        if is_preview_line(stripped):
            return True
        if is_conditional_compilation_line(stripped) or is_attribute_only_line(
            stripped
        ):
            continue
        return False
    return False


def normalize_preview_chunk(text: str) -> str:
    """Drop blank lines before a preview's closing brace (bad-split artifact)."""
    lines = text.splitlines()
    if not lines or not is_preview_line(lines[0].strip()):
        return text
    while len(lines) >= 2 and lines[-1].strip() == "}" and lines[-2].strip() == "":
        lines.pop(-2)
    return "\n".join(lines)


def normalize_preview_artifacts_in_file(text: str) -> str:
    lines = text.splitlines(keepends=True)
    i = 0
    n = len(lines)
    out: list[str] = []
    while i < n:
        stripped = lines[i].strip()
        if is_preview_line(stripped):
            start = i
            depth = 0
            started = False
            while i < n:
                for ch in lines[i]:
                    if ch == "{":
                        depth += 1
                        started = True
                    elif ch == "}":
                        depth -= 1
                i += 1
                if started and depth == 0:
                    break
            chunk = "".join(lines[start:i]).rstrip("\n")
            out.append(normalize_preview_chunk(chunk) + "\n")
        else:
            out.append(lines[i])
            i += 1
    return "".join(out)


def normalize_closing_brace_artifacts(text: str) -> str:
    """Drop blank lines between consecutive closing braces (reorder artifact)."""
    lines = text.splitlines()
    out: list[str] = []
    for i, line in enumerate(lines):
        if (
            line.strip() == ""
            and out
            and out[-1].strip().endswith("}")
            and i + 1 < len(lines)
            and lines[i + 1].strip() == "}"
        ):
            continue
        out.append(line)
    result = "\n".join(out)
    if text.endswith("\n"):
        result += "\n"
    return result


def normalize_file_artifacts(text: str) -> str:
    text = normalize_preview_artifacts_in_file(text)
    return normalize_closing_brace_artifacts(text)


# Reference types (and common UIKit / AppKit / Foundation classes) must not get
# ``-> Self`` / ``.init`` factory rewrites — including via ``extension``.
CLASS_TYPE_NAMES = frozenset(
    {
        "Bundle",
        "CALayer",
        "Calendar",
        "DateFormatter",
        "FileManager",
        "JSONDecoder",
        "JSONEncoder",
        "Locale",
        "MeasurementFormatter",
        "NSAttributedString",
        "NSMutableAttributedString",
        "NSObject",
        "NSString",
        "NumberFormatter",
        "NotificationCenter",
        "OperationQueue",
        "ProcessInfo",
        "Timer",
        "UIApplication",
        "UIBezierPath",
        "UIColor",
        "UIDevice",
        "UIFont",
        "UIFontMetrics",
        "UIImage",
        "UIScreen",
        "UITraitCollection",
        "UIView",
        "UIViewController",
        "URLSession",
        "UserDefaults",
    }
)


def is_class_type_name(type_name: str, kind: str = "") -> bool:
    if kind == "class":
        return True
    leaf = type_name.rsplit(".", 1)[-1]
    return leaf in CLASS_TYPE_NAMES


def apply_self_factory_conventions(body: str, type_name: str, kind: str = "") -> str:
    """Prefer ``-> Self`` / ``.init`` on factories of the enclosing type.

    Skips ``class`` types and extensions of known class types: ``-> Self``
    cannot return a value typed as the concrete class name (e.g. ``UIFont``
    from ``UIFontMetrics.scaledFont``), and ``static let x = .init(...)``
    loses contextual type on classes.

    Only rewrites constructors that pass arguments. Zero-argument
    ``TypeName()`` stays spelled out — inside an extension of that type,
    ``.init()`` often loses contextual type (e.g. ``Date()`` nested in
    ``Int(...)``, or ``NumberFormatter()`` in a closure assigned to a
    static let).

    Does not rewrite ``= TypeName(...)`` assignments (untyped
    ``static let`` / ``var`` bindings need the spelled type name).
    """

    if is_class_type_name(type_name, kind):
        return body

    escaped = re.escape(type_name)
    body = re.sub(
        rf"(static func\b[^{{]*?->\s*){escaped}(\?)?(?![.\w])",
        lambda match: f"{match.group(1)}Self{match.group(2) or ''}",
        body,
    )

    def replace_ctor(match: re.Match[str]) -> str:
        before = body[: match.start()].rstrip()
        if before.endswith("="):
            return match.group(0)
        return ".init("

    # Require a non-empty argument list so ``Date()`` / ``NumberFormatter()``
    # are not rewritten to ambiguous ``.init()``. ``)`` alone is not an argument.
    return re.sub(rf"\b{escaped}\s*\(\s*(?=[^)\s])", replace_ctor, body)


def apply_dot_init_inference(text: str) -> str:
    """Prefer ``.init`` when a parameter label or type annotation already supplies context.

    Does not insert ``: Type`` on untyped ``let`` / ``var`` bindings.
    """

    def replace_wrapped_value(match: re.Match[str]) -> str:
        return f"{match.group('wrapper')}(wrappedValue: .init("

    def replace_typed_assignment(match: re.Match[str]) -> str:
        if match.group("ctor") == match.group("type"):
            return f"{match.group('prefix')}.init("
        return match.group(0)

    def transform_chunk(chunk: str) -> str:
        chunk = DOT_INIT_WRAPPED_VALUE_RE.sub(replace_wrapped_value, chunk)
        chunk = DOT_INIT_TYPED_ASSIGN_RE.sub(replace_typed_assignment, chunk)
        return chunk

    if '"""' not in text:
        return transform_chunk(text)

    parts: list[str] = []
    i = 0
    while i < len(text):
        start = text.find('"""', i)
        if start == -1:
            parts.append(transform_chunk(text[i:]))
            break
        if start > i:
            parts.append(transform_chunk(text[i:start]))
        end = text.find('"""', start + 3)
        if end == -1:
            parts.append(text[start:])
            break
        parts.append(text[start : end + 3])
        i = end + 3
    return "".join(parts)


def extension_head_references_type(chunk: str, type_name: str) -> bool:
    head = first_code_line(chunk).split("{", 1)[0]
    return bool(re.search(rf"\b{re.escape(type_name)}\b", head))


def extension_wraps_file_type_in_sugar(chunk: str, stem: str) -> bool:
    """True for ``[Board.Vector]`` / ``Board.Vector?`` / ``Array<Board>``.

    A direct ``Board`` or ``Board.Status`` extension is not sugar. Those stay
    ahead of a collection or optional wrapped around the file type, so
    ``private extension [Board.Vector]`` sorts below ``extension Board: Collection``.
    A conformance is not sugar: ``extension Issue: ConversationRecord`` extends
    ``Issue``. A ``where`` clause on some other type
    (``Binding where Value == [AppRoute]``) is sugar.
    """
    if not is_extension_chunk(chunk):
        return False
    head = first_code_line(chunk).split("{", 1)[0]
    name = extension_extended_type_name(head).replace("`", "")
    if not name:
        return False
    bare = name[:-1].rstrip() if name.endswith("?") else name
    if re.fullmatch(rf"{re.escape(stem)}(?:\.\w+)*", bare):
        return bare != name
    if re.search(rf"\b{re.escape(stem)}\b", name):
        return True
    return bool(
        re.search(r"\bwhere\b", head) and re.search(rf"\b{re.escape(stem)}\b", head)
    )


def sort_main_file_chunks(chunks: list[str], stem: str) -> list[str]:
    def rank(chunk: str) -> tuple[int, int]:
        if is_type_declaration_chunk(chunk) and re.search(
            rf"\b(?:struct|class|enum|actor|protocol)\s+{re.escape(stem)}\b",
            first_code_line(chunk),
        ):
            return (0, 0)
        if is_extension_chunk(chunk) and extension_head_references_type(chunk, stem):
            return (0, 1)
        return (1, 0)

    indexed = list(enumerate(chunks))
    indexed.sort(key=lambda pair: (rank(pair[1]), pair[0]))
    ordered = [chunk for _, chunk in indexed]
    return _sort_extension_runs(ordered)


def preamble_sort_name(chunk: str) -> str:
    head = file_private_head(chunk)
    m = re.search(
        rf"\b(?:struct|class|enum|actor|protocol|extension)\s+({SWIFT_NAME})",
        head,
    )
    if m:
        return m.group(1).strip("`").lower()
    return head[:24].lower()


def extension_type_sort_key(chunk: str) -> tuple[tuple[str, ...], int]:
    """Extended-type path, then ``1`` when that type is optional.

    ``Issue`` sorts before ``Issue.Label``, and that group before
    ``PullRequest``. ``Square?`` sorts after ``Square``. Comparison is
    case-insensitive. A shorter path sorts before a longer path that starts
    with it, so a type stays above its nested types. Equal paths keep source
    order.
    """
    name = extension_extended_type_name(first_code_line(chunk)).replace("`", "")
    optional = name.endswith("?")
    if optional:
        name = name[:-1].rstrip()
    parts = tuple(part.lower() for part in re.findall(r"\w+", name))
    if not parts:
        return ((first_code_line(chunk)[:24].lower(),), 0)
    return (parts, int(optional))


def sort_extension_chunks(chunks: list[str]) -> list[str]:
    """Group by the extended type's root, then sort each group by path.

    Root groups keep the order those roots first appear, so unrelated
    extensions are not reshuffled. Within a group the parent stays above its
    nested types (``Issue`` before ``Issue.Label`` before ``Issue.User``).
    """
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    for chunk in chunks:
        parts, _optional = extension_type_sort_key(chunk)
        root = parts[0] if parts else ""
        if root not in groups:
            groups[root] = []
            order.append(root)
        groups[root].append(chunk)
    sorted_chunks: list[str] = []
    for root in order:
        sorted_chunks.extend(sorted(groups[root], key=extension_type_sort_key))
    return sorted_chunks


def _sort_extension_runs(chunks: list[str]) -> list[str]:
    """Path-sort each contiguous run of extensions; leave other chunks put."""
    result: list[str] = []
    run: list[str] = []

    def flush() -> None:
        result.extend(sort_extension_chunks(run))
        run.clear()

    for chunk in chunks:
        if is_extension_chunk(chunk):
            run.append(chunk)
        else:
            flush()
            result.append(chunk)
    flush()
    return result


def preamble_sort_key(chunk: str) -> tuple[tuple[str, ...], int]:
    """Name path, then ``0`` for a plain type and ``1`` for ``Type?``.

    ``private extension Square?`` sorts after ``private extension Square``.
    ``private extension Move`` sorts before ``private extension Move.Translation``.
    """
    if is_extension_chunk(chunk):
        return extension_type_sort_key(chunk)
    return ((preamble_sort_name(chunk),), 0)


def sort_preamble_chunks(chunks: list[str]) -> list[str]:
    return sorted(chunks, key=preamble_sort_key)


def pure_debug_if_interior(chunk: str) -> str | None:
    """Interior of one ``#if DEBUG`` … ``#endif`` block with no ``#else``.

    ``None`` when the chunk is not exactly that block. Directives inside
    string literals do not open or close the block.
    """
    lines = chunk.splitlines()
    if len(lines) < 2 or not is_debug_only_if(lines[0].strip()):
        return None

    depth = 0
    end: int | None = None
    state = StringLiteralState()
    saw_else = False
    for index, line in enumerate(lines):
        directive = None if state.in_literal else line.strip()
        if directive:
            if directive.startswith("#if"):
                depth += 1
            elif directive.startswith("#endif"):
                depth -= 1
                if depth == 0:
                    end = index
                    break
                if depth < 0:
                    return None
            elif depth >= 1 and (
                directive == "#else"
                or directive.startswith("#else ")
                or directive.startswith("#elseif")
                or directive.startswith("#elif")
            ):
                saw_else = True
        state = advance_string_literal_state(state, line)

    if saw_else or end is None or end != len(lines) - 1:
        return None
    if not lines[end].strip().startswith("#endif"):
        return None
    return "\n".join(lines[1:end]).strip("\n")


def _debug_interior_imports_then_rest(interior: str) -> tuple[list[str], list[str]]:
    """Split a DEBUG interior into import-only chunks and the remaining body."""
    members = split_top_level_members(interior)
    if not members:
        stripped = interior.strip("\n")
        return ([], [stripped] if stripped else [])
    imports = [m.strip("\n") for m in members if is_import_only_chunk(m)]
    rest = [m.strip("\n") for m in members if not is_import_only_chunk(m)]
    return imports, rest


def coalesce_adjacent_debug_chunks(chunks: list[str]) -> list[str]:
    """Share one ``#if DEBUG`` across a run of adjacent pure DEBUG chunks.

    File layout sorts first, then joins neighbors that share the condition:
    a preview helper and its fixtures, or a run of ``#Preview`` blocks.
    Imports from every joined interior move to the top of the shared wrapper
    (not into a second ``#if DEBUG``). A non-DEBUG chunk between them keeps
    the wrappers separate. Type-member reorder does not use this; those
    members stay individually wrapped.
    """
    out: list[str] = []
    index = 0
    count = len(chunks)
    while index < count:
        interior = pure_debug_if_interior(chunks[index])
        if interior is None:
            out.append(chunks[index])
            index += 1
            continue

        interiors = [interior]
        nxt = index + 1
        while nxt < count:
            following = pure_debug_if_interior(chunks[nxt])
            if following is None:
                break
            interiors.append(following)
            nxt += 1

        if len(interiors) == 1:
            out.append(chunks[index])
        else:
            imports: list[str] = []
            rest: list[str] = []
            for piece in interiors:
                piece_imports, piece_rest = _debug_interior_imports_then_rest(piece)
                imports.extend(piece_imports)
                rest.extend(piece_rest)
            # Preserve import order; drop exact duplicates from merged neighbors.
            unique_imports: list[str] = []
            seen_imports: set[str] = set()
            for item in imports:
                if item in seen_imports:
                    continue
                seen_imports.add(item)
                unique_imports.append(item)
            body = "\n\n".join(unique_imports + rest)
            out.append(f"#if DEBUG\n{body}\n#endif")
        index = nxt
    return out


def reorder_file_layout(text: str, path: Path) -> str:
    """Place file-private helpers before main types; extensions after main types.

    ``#Preview`` chunks that contain ``\"\"\"`` stay opaque via string-aware
    splitting; the rest of the file still reorders.
    """
    imports, rest = split_imports(text)
    if not rest.strip():
        return text

    chunks = split_top_level_members(rest)
    if not chunks:
        return text

    # Hoist bare / isolated import-only chunks into the preamble. A ``#if DEBUG``
    # import-only chunk that sits next to another pure DEBUG chunk stays put so
    # ``coalesce_adjacent_debug_chunks`` can share one wrapper (``UIColor+CSSHex``,
    # ``DebugKingfisherConfiguration``) instead of a second preamble DEBUG.
    import_chunks: list[str] = []
    kept_chunks: list[str] = []
    for index, chunk in enumerate(chunks):
        if not is_import_only_chunk(chunk):
            kept_chunks.append(chunk)
            continue
        debug_import = pure_debug_if_interior(chunk) is not None
        adjacent_debug = any(
            0 <= neighbor < len(chunks)
            and neighbor != index
            and pure_debug_if_interior(chunks[neighbor]) is not None
            for neighbor in (index - 1, index + 1)
        )
        if debug_import and adjacent_debug:
            kept_chunks.append(chunk)
        else:
            import_chunks.append(chunk)
    chunks = kept_chunks
    if import_chunks:
        extra = join_members(import_chunks)
        if imports.strip():
            imports = imports.rstrip("\n") + "\n\n" + extra
        else:
            imports = extra
        if not imports.endswith("\n\n"):
            imports = imports.rstrip("\n") + "\n\n"

    if not chunks:
        return imports if imports != text else text

    stem = path.stem
    preamble: list[str] = []
    mains: list[str] = []
    post: list[str] = []
    wrapped: list[str] = []
    tail: list[str] = []

    type_declarations = [c for c in chunks if is_type_declaration_chunk(c)]
    has_main_type_decl = any(
        re.search(
            rf"\b(?:struct|class|enum|actor|protocol)\s+{re.escape(stem)}\b",
            first_code_line(c),
        )
        for c in type_declarations
    )

    for chunk in chunks:
        if is_preview_chunk(chunk):
            tail.append(normalize_preview_chunk(chunk))
        elif is_private_fileprivate_chunk(chunk) and not (
            is_extension_chunk(chunk)
            and extension_head_references_type(chunk, stem)
        ):
            # Private extensions of the file's type, a nested type, or a
            # collection of either (``[Board.Vector]``) stay with the other
            # extensions. ``is_extension_chunk`` below places them.
            preamble.append(chunk)
        elif is_type_declaration_chunk(chunk):
            mains.append(chunk)
        elif is_extension_chunk(chunk):
            if not has_main_type_decl and extension_head_references_type(chunk, stem):
                mains.append(chunk)
            elif extension_wraps_file_type_in_sugar(chunk, stem):
                wrapped.append(chunk)
            else:
                post.append(chunk)
        else:
            tail.append(chunk)

    mains = sort_main_file_chunks(mains, stem)
    ordered = coalesce_adjacent_debug_chunks(
        sort_preamble_chunks(preamble)
        + mains
        + sort_extension_chunks(post)
        + sort_extension_chunks(wrapped)
        + tail
    )
    new_rest = join_members(ordered)
    if not new_rest:
        return text

    new_text = imports + new_rest
    if not imports and not new_rest.startswith("\n"):
        new_text = new_rest
    return new_text if new_text != text else text


def reorder_swiftui_body(body: str) -> str:
    members = split_top_level_members(body)
    if not members:
        return body

    body_members = [m for m in members if is_body_member(m)]
    other = [m for m in members if not is_body_member(m)]

    if len(body_members) != 1:
        return body

    body_member = body_members[0]
    before = sort_before_body_members(other)
    after = sort_after_body_members(
        [m for m in other if member_kind(m) in AFTER_BODY_KINDS or is_exposed_stored_property(m)]
    )
    consumed = set(before) | set(after)
    leftover = [m for m in other if m not in consumed]
    return join_members(before + [body_member] + after + leftover) or body


def reorder_plain_type_body(body: str, is_enum: bool) -> str:
    members = split_top_level_members(body)
    if not members:
        return body

    enum_cases = [m for m in members if is_enum and is_enum_case_member(m)]
    other = [m for m in members if m not in enum_cases]

    before = sort_before_body_members(other)
    consumed = set(before)
    remaining = [m for m in other if m not in consumed]

    after = sort_after_body_members(
        [m for m in remaining if member_kind(m) in AFTER_BODY_KINDS or is_exposed_stored_property(m)]
    )
    consumed |= set(after)
    leftover = [m for m in remaining if m not in consumed]

    return join_members(before + enum_cases + after + leftover) or body


def is_swiftui_type(header: str) -> bool:
    return bool(SWIFTUI_CONFORMANCE.search(header))


def extension_extended_type_name(header: str) -> str:
    """``Color``, ``[AppRoute]``, ``Binding`` — strips ``where`` / conformances."""
    match = re.search(r"\bextension\s+", header)
    if not match:
        return ""
    rest = header[match.end() :]
    rest = re.split(r"\bwhere\b", rest, maxsplit=1)[0]
    rest = rest.split(":", 1)[0]
    return rest.split("{", 1)[0].strip()


def find_type_spans(
    text: str,
    *,
    include_views: bool,
    include_non_views: bool,
) -> list[tuple[int, int, str, str, bool]]:
    spans: list[tuple[int, int, str, str, bool]] = []
    for m in TYPE_OR_EXTENSION_DECL_RE.finditer(text):
        brace_pos = find_next_brace_outside_strings(text, m.end())
        if brace_pos is None:
            continue

        header = text[m.start() : brace_pos + 1]
        if m.group(4) == "extension":
            kind = "extension"
            name = extension_extended_type_name(header)
        else:
            kind = m.group(2)
            name = m.group(3)
        is_view = is_swiftui_type(header)
        if is_view and not include_views:
            continue
        if not is_view and not include_non_views:
            continue

        close = find_matching_close_brace(text, brace_pos)
        if close is None:
            continue
        spans.append((brace_pos + 1, close, name, kind, is_view))
    return spans


def has_unresolved_conflict_markers(text: str) -> bool:
    """True when the file still contains Git conflict markers."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("<<<<<<<") or stripped.startswith(">>>>>>>"):
            return True
        if stripped == "=======":
            return True
    return False


def brace_counts(text: str) -> tuple[int, int]:
    return text.count("{"), text.count("}")


_FILE_SCOPE_INSTANCE_HEAD_RE = re.compile(
    r"^(?:(?:private|fileprivate|internal|public|package|open|override|"
    r"required|convenience|nonisolated|final|mutating|static|class)\s+)*"
    r"(?:func|init\??!?|deinit|var|let)\b"
)


def file_scope_instance_member_count(text: str) -> int:
    """Count instance-like members that sit at file scope, not in a type.

    Private / fileprivate helpers belong at file scope. A rising count of
    other ``func`` / ``init`` / ``deinit`` / ``var`` / ``let`` chunks means
    file-layout reorder pulled members out of a type.
    """
    _, rest = split_imports(text)
    count = 0
    for chunk in split_top_level_members(rest):
        if is_type_declaration_chunk(chunk) or is_extension_chunk(chunk):
            continue
        if is_preview_chunk(chunk) or is_private_fileprivate_chunk(chunk):
            continue
        if _FILE_SCOPE_INSTANCE_HEAD_RE.match(first_code_line(chunk)):
            count += 1
    return count


def rewrite_is_unsafe(original: str, text: str) -> bool:
    """Refuse a rewrite that looks like a splitter or merge-resolution bug."""
    if has_unresolved_conflict_markers(text):
        return True
    if brace_counts(text) != brace_counts(original):
        return True
    if original.count("{") != original.count("}"):
        return True
    return file_scope_instance_member_count(text) > file_scope_instance_member_count(
        original
    )


def process_file(
    path: Path,
    *,
    include_views: bool,
    include_non_views: bool,
    apply_dot_init: bool = True,
    write: bool = True,
) -> bool:
    original = path.read_text()
    if has_unresolved_conflict_markers(original) or original.count("{") != original.count(
        "}"
    ):
        return False

    text = reorder_file_layout(original, path)
    if rewrite_is_unsafe(original, text):
        text = original
    spans = find_type_spans(
        text, include_views=include_views, include_non_views=include_non_views
    )

    for start, end, name, kind, is_view in reversed(spans):
        original_body = text[start:end]
        body = original_body
        if apply_dot_init:
            body = apply_self_factory_conventions(body, name, kind)
        if is_view:
            new_body = reorder_swiftui_body(body)
        else:
            new_body = reorder_plain_type_body(body, is_enum=kind == "enum")

        if new_body != original_body:
            if not new_body.startswith("\n"):
                new_body = "\n" + new_body
            # Splitter bugs can drop a ``}`` and close the type early; skip that type.
            if (new_body.count("{"), new_body.count("}")) != (
                body.count("{"),
                body.count("}"),
            ):
                continue
            text = text[:start] + new_body + text[end:]

    text = format_enum_cases_in_text(text)
    text = normalize_file_artifacts(text)
    if apply_dot_init:
        text = apply_dot_init_inference(text)

    if text == original or rewrite_is_unsafe(original, text):
        return False

    if write:
        path.write_text(text)
    return True


SKIP_DIR_NAMES = frozenset({".build", ".git"})


def iter_swift_files(roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            if root.suffix == ".swift" and not is_skipped_path(root):
                files.append(root)
            continue
        if not root.is_dir():
            continue
        files.extend(
            path
            for path in sorted(root.rglob("*.swift"))
            if not is_skipped_path(path)
        )
    return files


def is_skipped_path(path: Path) -> bool:
    return any(part in SKIP_DIR_NAMES for part in path.parts)


def display_path(path: Path) -> Path:
    try:
        return path.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--views",
        action="store_true",
        help="Reorder SwiftUI View / ViewModifier / ToolbarContent types",
    )
    parser.add_argument(
        "--non-views",
        action="store_true",
        help="Reorder other struct / class / enum / actor / extension types",
    )
    parser.add_argument(
        "--no-dot-init",
        action="store_true",
        help="Skip replacing explicit type constructors with .init where inferrable",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if reordering would change any file (no writes)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print paths that would change (no writes)",
    )
    parser.add_argument(
        "roots",
        nargs="*",
        type=Path,
        help="Swift files or directories to scan (default: current directory)",
    )
    args = parser.parse_args(argv)

    include_views = args.views or not (args.views or args.non_views)
    include_non_views = args.non_views or not (args.views or args.non_views)
    write = not args.check and not args.dry_run

    changed_paths: list[Path] = []
    for path in iter_swift_files(args.roots or [Path(".")]):
        if process_file(
            path,
            include_views=include_views,
            include_non_views=include_non_views,
            apply_dot_init=not args.no_dot_init,
            write=write,
        ):
            print(display_path(path))
            changed_paths.append(path)

    if write:
        print(f"Updated {len(changed_paths)} files.")
    else:
        print(f"Would update {len(changed_paths)} files.")
    if args.check and changed_paths:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
