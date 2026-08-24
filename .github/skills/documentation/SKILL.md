---
name: documentation
description: Produce DIAGRAMS and binary document formats — Draw.io, PlantUML, PDF, Word. Use when the deliverable is a diagram (architecture, sequence, flowchart) or a .pdf/.docx file. For prose documentation in Markdown — README, design doc, ADR, guide — use `docs-writing` instead.
musubi-tier: substrate
expires-when: never (skills are the catalog the model pulls from)
triggers:
  - diagram
  - plantuml
  - drawio
  - architecture diagram
  - sequence diagram
  - flowchart
  - pdf
  - docx
---

## Purpose

Produce clear, maintainable technical documentation and architecture diagrams
in the format appropriate for the audience and toolchain.

## Choosing the Right Format

| Format | Best for | Tooling |
|--------|---------|---------|
| PlantUML | Sequence, class, component, activity diagrams — text-as-code, version-controllable | `render-plantuml.py` |
| Draw.io (`.drawio`) | Free-form architecture diagrams, network diagrams, whiteboard-style | Load `drawio-guide.md` |
| PDF | Final deliverables, reports, printed output | `render-pdf.py` |
| Word (`.docx`) | Collaborative documents, stakeholder reports with tracked changes | Load `pdf-word-guide.md` |

**Default to PlantUML** for any diagram that can be expressed as a relationship
(sequences, flows, components, classes). It is text, lives in git, and diffs cleanly.

Use Draw.io when stakeholders need to edit visually or when the diagram is
free-form and spatial layout matters.

Use PDF/Word only for final output to non-technical stakeholders.

## Procedure

### Producing a PlantUML diagram

1. Identify diagram type: sequence, component, class, activity, or state.
2. Write the `.puml` source following the patterns in `plantuml-guide.md`.
3. Run `musubi_run_asset("documentation", "render-plantuml.py", {"source": "...", "format": "svg"})`.
4. Asset returns the rendered file path or base64 content.

### Producing a Draw.io diagram

1. Load `drawio-guide.md` for XML structure and shape vocabulary.
2. Write the `.drawio` XML (it is plain XML — commit it to the repo).
3. Stakeholders open it in Draw.io desktop or app.diagrams.net.

### Producing a PDF

1. Write the document source in Markdown.
2. Run `musubi_run_asset("documentation", "render-pdf.py", {"source": "...", "title": "..."})`.
3. Asset uses `weasyprint` or `pandoc` to produce the PDF.

### Producing a Word document

1. Load `pdf-word-guide.md` for `python-docx` patterns.
2. Write document structure as a JSON spec.
3. Run `musubi_run_asset("documentation", "render-pdf.py", {"format": "docx", ...})`.

## Assets

`render-plantuml.py` — renders PlantUML source to SVG or PNG.
Input: `{"source": "@startuml\n...\n@enduml", "format": "svg"}`
Output: `{"ok": true, "path": "out/diagram.svg"}` or base64 content.
Use when: any PlantUML diagram needs to be rendered to an image.

`render-pdf.py` — converts Markdown or HTML source to PDF or DOCX.
Input: `{"source": "# Title\n...", "title": "Report", "format": "pdf|docx"}`
Output: `{"ok": true, "path": "out/report.pdf"}`
Use when: producing a final deliverable for stakeholders.

## When to Load References

- Load `drawio-guide.md` when: producing or editing a `.drawio` XML file
- Load `plantuml-guide.md` when: writing PlantUML source or unsure which diagram
  type to use for a given relationship
- Load `pdf-word-guide.md` when: producing PDF or Word output, or using `python-docx`
