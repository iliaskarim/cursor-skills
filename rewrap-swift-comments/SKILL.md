---
name: rewrap-swift-comments
description: Greedy-wrap Swift // and /// comments at 80 columns using Python len (Unicode-aware) via `rewrap_swift_comments.py` in this directory. Use when adding or editing comments, when comment wrap looks ragged or over-wide, or when the user asks to rewrap comments.
---

# Rewrap Swift Comments

Comment-wrap policy for agents and humans. The script is `rewrap_swift_comments.py` in this directory. Run it from the project you are editing.

## Run the script

From the project root (after this skill is linked into `~/.cursor/skills/`):

```bash
python3 ~/.cursor/skills/rewrap-swift-comments/rewrap_swift_comments.py
```

If the skill is not linked there, run `rewrap_swift_comments.py` by its path next to this file. With no arguments it scans the current directory for `.swift` files and skips `.build` (SwiftPM checkouts) and `.git`. Pass paths to limit scope:

```bash
python3 ~/.cursor/skills/rewrap-swift-comments/rewrap_swift_comments.py \
  Sources/Views/AsyncPagerView.swift
```

Flags:

- `--width N` — column limit (default 80)
- `--check` — exit 1 if any file would change (no writes)
- `--dry-run` — print paths that would change (no writes)

After running on Swift sources, format and lint as usual (`swiftformat`, then `swiftlint`) before committing.

## Rules

Column width is **Python `len` on decoded text**, not byte count, `awk` `length`, or SwiftLint’s 110. That matches `String.count` / the editor column for en-dashes, `·`, and smart quotes.

Each comment line takes as many **words** as fit. The next word starts a new line only when it would pass the width. `http://` / `https://` spans do not count toward that width (SwiftLint `line_length` `ignores_urls` / the `///.*https?://` exclude). Running the script twice is a no-op.

A “word” is whitespace-split, except these stay one token so they do not break mid-span:

- `"double-quoted"` and `“smart-quoted”` runs, glued to a preceding `e.g.` / `i.e.` so `e.g. "owner/repo · #1,234"` stays on one line
- `` `code` `` and `` ``doc-link`` ``
- Markdown `[label](url)` links (so a `SeeAlso` label does not wrap before `run](https://…)`). Whitespace inside `( dest )` is collapsed so a URL that was already on the next line rejoins as `[label](url)`
- `http://` / `https://` URLs

`///` and `//` are wrapped separately. A bare `///` or `//` line is a paragraph break. Consecutive list items (`- `, `* `, `1. `) stay separate paragraphs; a long item hangs wrap by the marker width. A `/// Word:` line with a capital (`Dark:`, `Keyframes:`) also starts a new paragraph so it is not joined onto the previous sentence. A lowercase leftover like `identity:` still joins.

A trailing `,` / `.` glues to the previous token so a wrap cannot start with `/// ,`.

## Left alone

- `Package.swift` (SPM `swift-tools-version` comments are not prose)
- Any path under `.build/` (local SwiftPM checkouts)
- Trailing comments on a code line (`let x = 1 // keep`)
- `/* … */` block comments
- `// LABEL: …` lines (`INFO:`, `ERROR:`, `MARK:`, `TODO:`, `swiftlint:`, `swift-tools-version:`, and other `Word:` prefixes)
- `// swiftformat:`, `// sourcery:`, `// swift-format`, `//:`
- Preformatted comment paragraphs (any content line whose text starts with two or more spaces — indented samples). A list continuation hung by the marker width (`- ` → two spaces) is prose and reflows; a nested marker (`  - item`) or a deeper indent stays left alone

A single token longer than the width stays on its own line, except a URL does not count so `See https://…/very-long-path` can stay one line.

## Tests

```bash
python3 ~/.cursor/skills/rewrap-swift-comments/test_rewrap_swift_comments.py
```
