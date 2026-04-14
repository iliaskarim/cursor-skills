---
name: build-source-pdf
description: Build a single PDF from repository source files and tests with stable monospace formatting, page breaks between files, and page numbers in the bottom-left corner. Use when the user asks to export/print source code to PDF, include tests, regenerate a code listing PDF, or adjust code-PDF typography/layout.
---
# Build Source PDF

## Purpose

Generate a combined code listing:
- `ChessCore-all-sources.txt`
- `ChessCore-all-sources.pdf`

Use SF Mono Bold, preserve whitespace, insert page breaks between files, and add bottom-left page numbers.

## Default Workflow

1. Run the project script from repo root:

```bash
python3 scripts/export_sources_pdf.py
```

2. Confirm output message indicates both files were written.

## Formatting Rules

- Keep body text and footer page numbers at the same point size (`BODY_PT`).
- Keep separator line length configurable via `FILE_RULE_LEN`.
- Preserve code spacing by avoiding justified text:
  - Use left alignment (`Align.L`)
  - Use character wrapping (`WrapMode.CHAR`) for long code tokens
- Keep one file per section and separate sections with form feed (`\f`) so each file starts on a new page.

## What To Edit For User Requests

- **Font size**: change `BODY_PT` in `scripts/export_sources_pdf.py`
- **Separator length**: change `FILE_RULE_LEN`
- **Footer placement**: tune `FOOTER_MARGIN_MM`
- **Footer style**: update `SourceListingPDF.footer()`

After any change, regenerate:

```bash
python3 scripts/export_sources_pdf.py
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
