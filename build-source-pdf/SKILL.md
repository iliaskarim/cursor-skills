---
name: build-source-pdf
description: Build a single PDF from repository source files and tests, from one commit's patch, or from the commits unique to each of two diverging branches such as develop and master. Uses stable monospace formatting, page breaks between sections, and page numbers in the bottom-left corner. Use when the user asks to export/print source code to PDF, include tests, regenerate a code listing PDF, export a commit or a branch divergence, or adjust code-PDF typography/layout.
---
# Build Source PDF

## Purpose

Generate a combined code listing (`{PackageName}` is `name:` in root `Package.swift`):

- `{PackageName}-all-sources.txt`
- `{PackageName}-all-sources.pdf`

Or generate a commit patch export:

- `{PackageName}-commit-{short-hash}-patch.txt`
- `{PackageName}-commit-{short-hash}-patch.pdf`

Or generate the divergence of two branches (commits reachable from only one side):

- `{PackageName}-{left}-{left-short}-vs-{right}-{right-short}-patch.txt`
- `{PackageName}-{left}-{left-short}-vs-{right}-{right-short}-patch.pdf`

Use SF Mono Bold, preserve whitespace, insert page breaks between sections, and add bottom-left page numbers.

## Default Workflow

1. `cd` into the Swift package root (must contain `Package.swift`).
2. Run:

```bash
python3 ~/.cursor/skills-cursor/build-source-pdf/export_sources_pdf.py
```

Optional: pass the package directory if you are not already in it:

```bash
python3 ~/.cursor/skills-cursor/build-source-pdf/export_sources_pdf.py /path/to/YourPackage
```

Optional: export a commit patch instead of the full source listing:

```bash
python3 ~/.cursor/skills-cursor/build-source-pdf/export_sources_pdf.py --commit <commit-ish>
```

When exporting a package outside the current directory:

```bash
python3 ~/.cursor/skills-cursor/build-source-pdf/export_sources_pdf.py /path/to/YourPackage --commit <commit-ish>
```

Optional: export two branches that diverge. The first name is the left side and the second is the right side, matching `git log left...right`:

```bash
python3 ~/.cursor/skills-cursor/build-source-pdf/export_sources_pdf.py --branches develop master
```

When the package is outside the current directory:

```bash
python3 ~/.cursor/skills-cursor/build-source-pdf/export_sources_pdf.py /path/to/YourPackage --branches develop master
```

Pass `--commit` or `--branches`, and only one of them. The branch export still works when one tip contains the other. Each side is the commits reachable from that tip and not from the other (`right..left`, then `left..right`), with `git log --format=fuller --stat --patch`. The text has three form-feed sections, so each starts on its own page:

- Cover: both tips, the merge-base (or unrelated histories), commit counts, and `git diff --stat` from the merge-base to each tip.
- Commits on the first branch and not the second.
- Commits on the second branch and not the first.

Branch names in the filename keep letters, digits, `.`, `_`, and `-`. Any other character, including `/`, becomes `-`.

3. Confirm the log shows both output files were written in that directory.
   Source listings are named `{PackageName}-all-sources.{txt,pdf}`;
   commit patches are named `{PackageName}-commit-{short-hash}-patch.{txt,pdf}`;
   branch divergences are named `{PackageName}-{left}-{left-short}-vs-{right}-{right-short}-patch.{txt,pdf}`.

## Formatting Rules

- Keep body text and footer page numbers at the same point size (`BODY_PT`).
- Keep separator line length configurable via `FILE_RULE_LEN`.
- Preserve code spacing by avoiding justified text:
  - Use left alignment (`Align.L`)
  - Use character wrapping (`WrapMode.CHAR`) for long code tokens
- Keep one file per section and separate sections with form feed (`\f`) so each file starts on a new page.
- A branch divergence uses the same form feed between its cover and each side's commit log. Commits within a side stay in one section.

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

The export should include:
- `Package.swift` (if present)
- all `Sources/**/*.swift`
- all `Tests/**/*.swift`

Sorted deterministically.

## Validation Checklist

- PDF regenerated successfully
- No stretched/inter-word spacing artifacts
- Each file starts on a new page
- Page number appears bottom-left on every page
- Font family is SF Mono Bold
