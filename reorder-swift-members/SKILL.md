---
name: reorder-swift-members
description: Reorder Swift type members to the layout rules in this skill using `reorder_swift_members.py` in this directory. Use when adding or editing Swift types, when member order looks wrong, when enum case layout or spacing looks wrong, or when the user asks to reorder properties, methods, or apply member layout conventions.
---

# Reorder Swift Members

Layout policy for agents and humans. The script is `reorder_swift_members.py` in this directory. Run it from the project you are editing.

## Run the script

From the project root (after this skill is linked into `~/.cursor/skills/`):

```bash
python3 ~/.cursor/skills/reorder-swift-members/reorder_swift_members.py
```

If the skill is not linked there, run `reorder_swift_members.py` by its path next to this file. With no arguments it scans the current directory for `.swift` files and skips `.build` and `.git`. Pass paths to limit scope:

```bash
python3 ~/.cursor/skills/reorder-swift-members/reorder_swift_members.py \
  --views --non-views Sources/Navigation/WebURL.swift
```

Flags:

- `--views` — SwiftUI `View`, `ViewModifier`, `ToolbarContent`
- `--non-views` — other `struct`, `class`, `enum`, `actor`, and `extension` bodies
- Omit both flags to run both (default)
- `--check` — exit 1 if reordering would change any file (no writes)
- `--dry-run` — print paths that would change (no writes)

After running, build and spot-check types with exposed stored properties (`let`/`var` used in memberwise inits) and files that mix private helpers with a main type.

## When not to run

Do **not** run this script as post-merge cleanup, on a file that still has conflict markers, or on a file you just resolved until it compiles. File-layout reorder can pull members out of a type when braces or declarations are mid-edit — that is how `Pager.swift` was rewritten after a merge (stray `.init`, members at file scope, `deinit` / `load` folded into `convenience init`).

The script skips a file (does not write) when:

- it contains unresolved `<<<<<<<` / `=======` / `>>>>>>>` markers
- `{` / `}` counts would change
- file-layout would hoist instance members (`func` / `init` / `deinit` / `var` / `let`) to file scope

Those guards do not make a just-resolved file safe to reorder. After a merge, leave those files alone unless you are intentionally fixing member order on a type that already compiles.

## File layout

The script applies this order (after imports). It is not a separate manual step.

Imports stay first, including isolated ``#if DEBUG`` / ``#endif`` wrappers that contain only ``import`` or ``@testable import`` lines (e.g. before a non-DEBUG type). A DEBUG import-only chunk that sits next to another pure DEBUG chunk stays with that neighbor so they share one wrapper — do not split a DEBUG-only import and a neighboring DEBUG type into a preamble import and a second DEBUG type. Mixed DEBUG (imports plus types) stays one wrapper with imports on top.

Per file:

1. `private` / `fileprivate` helpers and extensions (types that do not depend on the file’s main type), sorted by name within this group
2. Main type(s) the file is named for (`struct` / `class` / `enum` / `actor` / `protocol`, or in `Extensions/` an `extension` on the filename type when there is no primary declaration)
3. Other `extension` blocks (e.g. `extension [AppRoute]` after `enum AppRoute`, `extension View` toolbar helpers after the view types). A `private` / `fileprivate` extension whose head names the file’s type stays here too, including a nested type and collection sugar (`private extension Board.Vector`, `private extension [Board.Vector]` after `struct Board`). It is not a preamble helper.
4. `#Preview` blocks last

Import-only `#if` / `#endif` blocks (`#if DEBUG`, `#if canImport`, …) stay in the import preamble. They must not be left behind when a private helper is hoisted, and they must not be ranked as leftover chunks.

`#Preview` chunks that contain multiline `"""` stay opaque (diff literals); other top-level chunks in the file still reorder. Member reorder treats `"""…"""` as opaque so braces inside GraphQL / HTML / JS do not split members. Adjacent file-scope chunks under the same pure `#if DEBUG` share one wrapper instead of a redundant `#endif` / `#if DEBUG` pair. That includes a preview-only helper and its fixtures, and a run of `#Preview` blocks.

Extensions on shared types (`View`, `String`, models, etc.) belong in an `Extensions/` directory, not at the bottom of arbitrary view files. The script reorders within a file; moving a shared extension to `Extensions/` is still a manual relocation when adding or cleaning up files.

## Layout rules

### SwiftUI types (`View`, `ViewModifier`, `ToolbarContent`)

**Before `body`** (kind first, then ACL, then name within each subgroup — a private nested type stays ahead of an internal static var):

1. Nested types (`typealias`, `associatedtype`, `struct`, `class`, `enum`)
2. Static vars/lets
3. Static funcs

**After `body`:**

1. Non-private instance vars/lets — public, then package, then internal. Within each ACL, exposed **stored** properties keep relative declaration order; other vars (e.g. computed) insert by name (`contains…` before `download…`). An internal `pathComponent` stays after all public vars/lets.
2. Private instance vars/lets
3. Internal instance funcs
4. `init` (including failable `init?` / `init!`)
5. `deinit`
6. Private instance funcs

### Other types (`struct`, `class`, `enum`, `actor`, `protocol`, `extension`)

Same as above, except there is no `body`. Enum cases keep declaration order between the static/type section and instance members (nested enums too). `associatedtype` sorts with nested types, by name. Protocol bodies use this same order as struct / class / enum / actor bodies. `extension` bodies use this same order — static members first, then instance vars, then instance funcs, then `init` (a failable `init` comes after the static helpers).

Public computed properties — including `Endpoint` witnesses — sort by name among non-stored **public** instance vars and insert into the public exposed-stored sequence by name: `httpHeaderFields`, then `urlHost`, `urlPath`, `urlPort`, `urlQueryItems`, `urlScheme`. A later public stored `let` must not hoist ahead of an alphabetically earlier public computed var (`downloadURL` after `containsGitLFSPointer`). Default-internal witnesses such as `pathComponent` stay after every public var/let. A `let` inside a getter does not make the property stored and must not hoist it ahead of alphabetically earlier computed vars.

### Enum cases

Apply to every `enum`, including nested ones. There is no SwiftLint / SwiftFormat rule for this (leave `wrapEnumCases` disabled so Format does not split comma lists).

**Declarations**, not names: a blank line between each `case` statement. Do not put a blank line between wrapped continuation lines of one statement.

- **No associated values and no raw value** (`case a`, `case a, b`): one comma-separated statement (`case a, b, c`). Wrap at 110 columns (SwiftLint `line_length`). Wrap continuations align with the first name (SwiftFormat `indent`) and the continued line ends with a comma.
- **Raw values** (`case a = 1`, `case scheme = "CALLBACK_URL_SCHEME"`): one `case` per declaration, blank line before the next `case`. Do not write `case a = 1, b = 2`.
- **Associated values** (`case foo(Bar)`, `indirect case foo(Bar)`): one `case` per declaration, blank line before the next `case`.
- **Comments**: do not merge a case that has its own `///` / `//` onto a shared comma line. Those stay separate declarations, with a blank line between them.
- **Mix**: keep source order; do not alphabetize. Adjacent simple names share a statement. Each raw-value, associated, or commented `case` is its own declaration, with a blank line before the next.

```swift
enum SearchCategory: String {
  case repos, users
}

enum Mode {
  case graphicSnapshot, interactivePreview
}

enum InfoDictionaryKey: String {
  case callbackURLScheme = "CALLBACK_URL_SCHEME"

  case oauthClientID = "OAUTH_CLIENT_ID"

  case oauthClientSecret = "OAUTH_CLIENT_SECRET"
}

enum Event {
  case push, fork

  /// Docs keep this declaration separate.
  case release

  case issue(Issue)

  case pullRequest(PullRequest)
}
```

### ACL notes

- `private(set)` / `fileprivate(set)` count as **internal** for grouping (read access is internal).
- Within each ACL, **vars/lets always precede funcs** — never interleave.
- Nested types are sorted alphabetically by name, separate from static members (types first, then static vars, then static funcs; ACL then name within each kind).

### `#if DEBUG` members

Do **not** keep a multi-member `#if DEBUG` … `#endif` block as one opaque unit. Split the members out, sort them with everything else as if the directive were absent, then wrap **each** DEBUG-only member in its own `#if DEBUG` / `#endif` (directives at column 0). Blocks that contain `#else` / `#elseif` stay opaque.

This applies to type members. File-scope import-only `#if DEBUG` stays in the import preamble when it is isolated (see File layout). Adjacent file-scope chunks that are all pure `#if DEBUG` (no `#else`) share one wrapper — imports from those chunks move to the top of the shared block. That includes a preview-only helper and the fixtures it reads, a run of `#Preview` blocks, and DEBUG files like `DebugKingfisherConfiguration`. A `#endif` immediately followed by `#if DEBUG` between those neighbors is redundant. A DEBUG-only helper stays in its own wrapper when a non-DEBUG type or helper sits between it and the previews. Preview-only helpers are DEBUG-only: they do not ship in release.

```swift
#if DEBUG
  static let debug = UserSession(accessToken: "…")
#endif

  static func restoreFromKeychain() -> UserSession { … }

#if DEBUG
  private static func debugOptionalUserDetailLoadFailure() async throws { … }
#endif
```

### Factory methods (`Self` + `.init`)

Static factories that return the type they are defined on use ``Self`` (or ``Self?``) and construct with ``.init``:

```swift
static func parse(owner: String, name: String, branch: String?) -> Self {
  .init(owner: owner, name: name, branch: branch, showsOverview: true, isTag: false)
}

static func parse(owner: String, repo: String, branch: String, path: String) -> Self? {
  guard !path.isEmpty else {
    return nil
  }

  return .init(owner: owner, repo: repo, path: path, branch: branch, filename: filename)
}
```

Do not write ``-> RepoRoute`` or ``return RepoRoute(...)``. Methods that return a *different* type (``AppRoute``, ``URL``, ``String``) keep that type name.

Do **not** apply ``Self`` / ``.init`` factory rewrites on ``class`` types — including ``extension`` of UIKit / AppKit / Foundation classes (``UIFont``, ``UIColor``, ``UIView``, …). ``-> Self`` cannot return a value typed as the concrete class name (e.g. ``UIFontMetrics.scaledFont`` returns ``UIFont``), and ``static let debug = .init(...)`` has no contextual type. Keep ``-> UserSession`` / ``UserSession(...)`` and ``-> UIFont`` / ``UIFont(...)``.

Never write an untyped ``static let`` / ``static var`` as ``= .init(...)``. That does **not** compile — not for structs, not for ``Notification.Name``:

```swift
// Does not compile — no contextual type for `.init`.
extension Notification.Name {
  static let didDismiss = .init("DidDismiss")
}

// OK — spelled type, `Self(...)`, or an explicit type annotation.
extension Notification.Name {
  static let didDismiss = Notification.Name("DidDismiss")
  static let didPresent = Self("DidPresent")
  static let didFail: Notification.Name = .init("DidFail")
}
```

Keep zero-argument ``TypeName()`` spelled out inside an extension of that type. Rewriting ``Date()`` / ``NumberFormatter()`` to ``.init()`` often loses contextual type (e.g. ``Int(Date().timeIntervalSince(self))``, or ``let formatter = NumberFormatter()`` in a static-let closure). The Self-factory script pass only rewrites constructors that pass arguments, and does not rewrite ``= TypeName(...)`` assignments (untyped ``static let`` / ``var`` bindings need the spelled name so they do not become illegal ``= .init(...)``).

### `.init` inference

Use ``.init`` only when Swift **already** knows the type. Do not add a type annotation so you can drop the name from the constructor.

```swift
// Keep — the constructor is what names the type.
let route = AppRoute(CommitsRoute(...))

// Do not write this just to use `.init`.
let route: AppRoute = .init(CommitsRoute(...))
```

Rewrite ``Type(...)`` to ``.init(...)`` when inference is already there:

- Return type: ``func destination() -> AppRoute { .init(...) }``
- Argument / property type: ``open(.init(...))``, ``pendingRoute = .init(...)``
- Property-wrapper backing storage: ``StateObject(wrappedValue: Pager(...))`` → ``StateObject(wrappedValue: .init(...))``
- Typed assignments that already have ``: Type``: ``let x: Foo = Foo(bar: 1)`` → ``let x: Foo = .init(bar: 1)``
- Constructors of the enclosing type: ``return FileRoute(...)`` → ``return .init(...)``
- Static factories of the enclosing type: ``-> FileRoute`` / ``-> FileRoute?`` → ``Self`` / ``Self?``

Keep the type name on ``NavigationLink(value:)``. That initializer is generic over ``P?``, so ``value: .init(PullRequestDetailRoute(...))`` becomes ``Optional`` of the inner route — not ``AppRoute`` — and the link cannot activate (no ``navigationDestination`` for that type). Write ``value: AppRoute(PullRequestDetailRoute(...))``.

The script’s rewrite pass covers the last four (wrapped value, existing ``: Type =``, enclosing-type constructors **with arguments**, ``Self`` factories). It does **not** rewrite zero-argument ``TypeName()`` to ``.init()``. Apply the same rule by hand at other inferable sites. Use ``--no-dot-init`` to skip the pass. It is intentionally conservative (capitalized type names only; no module-qualified, chained, or inferred ``@State``/``@StateObject`` defaults).

## Multiline strings

Brace / paren matching treats `"""…"""`, ordinary `"…"`, and raw `#"…"#` / `#"""…"""#` literals as opaque. Types with embedded GraphQL, HTML, or JS are reordered like any other type.

## When editing manually

Apply the same groups by hand for files the script skips or for partial fixes. Do not alphabetize exposed stored `let` properties that define memberwise init parameter order. Do alphabetize public computed properties (`httpHeaderFields` before `urlPath`). Apply `.init` only where the type is already inferred; do not add a `: Type` annotation to drop the name from the constructor.

## Tests

```bash
python3 ~/.cursor/skills/reorder-swift-members/test_reorder_swift_members.py
```

## Verify

Build the project you edited. Check a view with `@ScaledMetric` private vars, an endpoint with nested `typealias` / `Response`, a model with `decodingStrategies`, and an enum with static helpers.
