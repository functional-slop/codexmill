# 16. Exports: Word (.docx) and Obsidian/Scrivener (.zip)

Date: 2026-07-10 · Status: accepted · Roadmap: D (output polish + exports)

## Context
The bible could leave the app only as Markdown (download / copy) or a printed PDF. Writers work in
Word, Obsidian, and Scrivener; handing them one flat `.md` makes them do the conversion. Roadmap D
calls for real exports.

## Decision
A new `codexmill/export.py` builds two formats from a stored `StoryBible`, served by
`GET /api/bibles/{id}/export?format=docx|obsidian` (owner-scoped, same auth as the other bible
routes):
- **`.docx`** assembled directly from the schema with `python-docx` (headings/lists map to real
  Word styles), not by parsing Markdown.
- **`.zip`** of per-section Markdown files (`00-overview.md` + one file per section) under a folder
  named after the bible. Obsidian and Scrivener both import a folder of Markdown cleanly, so one
  artifact covers both.

`render.py` was refactored so `bible_sections()` is the single source of section content, shared by
the flat Markdown bundle and the Obsidian export (no duplicated section logic). `python-docx` is a
pure-Python dep (fine on aarch64) and is imported lazily inside `to_docx`.

## Consequences
- Exports need a *saved* bible (they read the library by id), so the "See a sample" view (no id)
  disables the export buttons; generated and opened bibles enable them.
- One new runtime dependency (`python-docx`, pulls `lxml`).
- Section rendering is now centralized; a new section is added once in `bible_sections` and appears
  in the Markdown bundle and the Obsidian zip automatically (the docx builder is still explicit).
