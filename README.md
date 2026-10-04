# Cursor skills

Agent skills for Cursor. Each top-level directory is one skill: a `SKILL.md` (YAML `name` and `description`, then the instructions) plus any scripts that skill runs. Cursor uses the description to decide when to load the skill.

## Install

User skills load from `~/.cursor/skills/<name>/`. From a clone of this repo:

```bash
mkdir -p ~/.cursor/skills
for dir in */; do
  [ -f "${dir}SKILL.md" ] || continue
  ln -sfn "$PWD/${dir%/}" "$HOME/.cursor/skills/${dir%/}"
done
```

`build-source-pdf` calls its exporter at `~/.cursor/skills-cursor/build-source-pdf/export_sources_pdf.py`, so link that skill there as well:

```bash
mkdir -p ~/.cursor/skills-cursor
ln -sfn "$PWD/build-source-pdf" ~/.cursor/skills-cursor/build-source-pdf
```

Once linked, a skill is available in any project. Scripts live next to `SKILL.md`, so the project does not need another checkout.

## Skills

### [build-source-pdf](build-source-pdf/SKILL.md)

Build one PDF of a Swift package’s sources and tests: SF Mono Bold, a page break between files, page numbers in the bottom-left corner. The exporter is `export_sources_pdf.py` in this directory.

### [reorder-swift-members](reorder-swift-members/SKILL.md)

Reorder Swift type members (nested types, static members, enum cases, then instance members). Run `reorder_swift_members.py` from the project you are editing. With no arguments it scans the current directory.

### [rewrap-swift-comments](rewrap-swift-comments/SKILL.md)

Greedy-wrap Swift `//` and `///` comments at 80 columns using Python `len`. Run `rewrap_swift_comments.py` from the project you are editing. With no arguments it scans the current directory.

## Adding a skill

Add a directory whose name matches the skill `name`, with a `SKILL.md` at its root. Put helper scripts next to that file when the skill owns them.
