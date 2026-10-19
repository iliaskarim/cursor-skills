---
name: build-source-pdf
description: Build a single PDF from repository files, from one commit's patch, or from the combined diff of two branches such as develop and master. Choose --files sources (Sources/), tests (Tests/), or all (Sources, Tests, Justfile, README, scripts, and other text files). Ignores dotfiles and paths under dot directories. Uses stable monospace formatting, page breaks between sections, and page numbers in the bottom-left corner. Use when the user asks to export/print source code to PDF, include or exclude tests, regenerate a code listing PDF, export a commit or a branch divergence, or adjust code-PDF typography/layout.
---
# Build Source PDF

## Purpose

Generate a combined code listing (`{PackageName}` is `name:` in root `Package.swift`). `{scope}` is `sources`, `tests`, or `all-files`:

- `{PackageName}-{scope}.txt`
- `{PackageName}-{scope}.pdf`

Or generate a commit patch export:

- `{PackageName}-commit-{short-hash}-{scope}-patch.txt`
- `{PackageName}-commit-{short-hash}-{scope}-patch.pdf`

Or generate one combined diff per side of two branches (every commit on that side, together):

- `{PackageName}-{left}-{left-short}-vs-{right}-{right-short}-{scope}-patch.txt`
- `{PackageName}-{left}-{left-short}-vs-{right}-{right-short}-{scope}-patch.pdf`

Use SF Mono Bold, preserve whitespace, insert page breaks between sections, and add bottom-left page numbers.

## Default Workflow

1. `cd` into the Swift package root (must contain `Package.swift`).
2. Run one of:

```bash
python3 ~/.cursor/skills-cursor/build-source-pdf/export_sources_pdf.py --files sources
python3 ~/.cursor/skills-cursor/build-source-pdf/export_sources_pdf.py --files tests
python3 ~/.cursor/skills-cursor/build-source-pdf/export_sources_pdf.py --files all
```

`--files` chooses the paths. The default is `all`.

- `sources`: files under `Sources/`
- `tests`: files under `Tests/`
- `all`: every text file except dotfiles, including `Sources/`, `Tests/`, `Package.swift`, `Justfile`, `README`, and `scripts/`

`--files` applies to the source listing, to `--commit`, and to `--branches`.

Optional: pass the package directory if you are not already in it:

```bash
python3 ~/.cursor/skills-cursor/build-source-pdf/export_sources_pdf.py /path/to/YourPackage --files sources
```

Optional: export a commit patch instead of the full source listing:

```bash
python3 ~/.cursor/skills-cursor/build-source-pdf/export_sources_pdf.py --commit <commit-ish> --files sources
```

Optional: export two branches that diverge. The first name is the left side and the second is the right side:

```bash
python3 ~/.cursor/skills-cursor/build-source-pdf/export_sources_pdf.py --branches develop master --files sources
```

Pass `--commit` or `--branches`, and only one of them. The branch export still works when one tip contains the other. Each side that has commits is one `git diff --stat --patch` from the merge-base to that tip, so those commits appear as a single diff. A side with no commits is omitted. Unrelated histories use `git diff` between the two tips. Form feed separates the cover from each diff, so each starts on its own page:

- Cover: both tips, the merge-base (or unrelated histories), and the commit counts.
- Combined diff of the first branch since the merge-base, when that branch has commits.
- Combined diff of the second branch since the merge-base, when that branch has commits.

Branch names in the filename keep letters, digits, `.`, `_`, and `-`. Any other character, including `/`, becomes `-`.

3. Confirm the log shows both output files were written in that directory.
   `{scope}` is `sources`, `tests`, or `all-files`.
   Source listings are named `{PackageName}-{scope}.{txt,pdf}`;
   commit patches are named `{PackageName}-commit-{short-hash}-{scope}-patch.{txt,pdf}`;
   branch divergences are named `{PackageName}-{left}-{left-short}-vs-{right}-{right-short}-{scope}-patch.{txt,pdf}`.

## Formatting Rules

- Keep body text and footer page numbers at the same point size (`BODY_PT`).
- Keep separator line length configurable via `FILE_RULE_LEN`.
- Preserve code spacing by avoiding justified text:
  - Use left alignment (`Align.L`)
  - Use character wrapping (`WrapMode.CHAR`) for long code tokens
- Keep one file per section and separate sections with form feed (`\f`) so each file starts on a new page.
- A branch divergence uses the same form feed between its cover and each side's combined diff.

## What To Edit For User Requests

- **Font size**: change `BODY_PT` in `~/.cursor/skills-cursor/build-source-pdf/export_sources_pdf.py`
- **Separator length**: change `FILE_RULE_LEN`
- **Footer placement**: tune `FOOTER_MARGIN_MM`
- **Footer style**: update `SourceListingPDF.footer()`

After any change, regenerate:

```bash
python3 ~/.cursor/skills-cursor/build-source-pdf/export_sources_pdf.py
```

## Scope of Source Collection

`--files` selects the paths, in source listings, commit patches, and branch diffs:

- `sources`: `Sources/**`
- `tests`: `Tests/**`
- `all`: every tracked text file, including `Package.swift`, `Justfile`, `README`, and `scripts/`

Listings read `git ls-files` and skip binaries. Diffs use the same roots as a git pathspec. Paths are sorted deterministically.

Ignore dotfiles in every mode. Skip any path whose name starts with `.`, and any file under a directory whose name starts with `.`. That includes `.gitignore`, `.swiftformat`, `.swiftlint.yml`, and files under `.github/` or `.vscode/`.

## Validation Checklist

- PDF regenerated successfully
- No stretched/inter-word spacing artifacts
- Each file starts on a new page
- Page number appears bottom-left on every page
- Font family is SF Mono Bold
