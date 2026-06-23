# PDF and Word Guide — Documentation Skill Reference

Use this reference when producing PDF or DOCX output for stakeholders, or when
using `python-docx` to build Word documents programmatically.

---

## When to Produce PDF vs Word

| Format | Use when |
|--------|---------|
| PDF | Final deliverable, print-ready, no further editing expected |
| Word (.docx) | Collaborative, needs track-changes, stakeholder will edit |

Both formats are produced by `render-pdf.py` asset. PDF requires `pandoc` (preferred)
or `weasyprint`. DOCX requires `pandoc` or `python-docx`.

---

## Producing PDF via render-pdf.py

Input to `musubi_run_asset`:

```json
{
    "source": "# Title\n\nBody text...",
    "title": "Musubi — Architecture Report",
    "format": "pdf"
}
```

The asset will:
1. Try `pandoc` first (best quality, handles tables and code blocks)
2. Return `{"ok": true, "path": "/tmp/tmpXXX.pdf", "size_bytes": 12345}`

The returned path is a temporary file. Move or read it before the process exits.

### pandoc setup

```bash
# Debian/Ubuntu
sudo apt-get install pandoc texlive-xetex

# macOS
brew install pandoc
```

For PDF output, pandoc requires a LaTeX engine. The asset uses the default
engine (pdflatex). To use xelatex (better Unicode support), add to the source:

```markdown
---
mainfont: "DejaVu Serif"
---
```

---

## Producing DOCX via render-pdf.py

```json
{
    "source": "# Section\n\nParagraph.\n\n- bullet\n- bullet",
    "title": "Technical Spec",
    "format": "docx"
}
```

The asset prefers `pandoc` for DOCX. Falls back to `python-docx` if pandoc is
not installed.

---

## python-docx — Direct Usage

Use `python-docx` when you need precise control over document structure, styles,
or tables that `pandoc` cannot produce from Markdown.

### Install

```bash
pip install python-docx
```

### Basic document

```python
from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

doc = Document()

# Title
doc.add_heading("Musubi — Architecture Report", level=0)

# Paragraph
p = doc.add_paragraph("This document describes the harness architecture.")

# Heading levels
doc.add_heading("Overview", level=1)
doc.add_heading("Components", level=2)

# Bullet list
doc.add_paragraph("server.py — MCP entry point", style="List Bullet")
doc.add_paragraph("state.py — session state", style="List Bullet")

# Numbered list
doc.add_paragraph("Run musubi_new_session", style="List Number")
doc.add_paragraph("Run musubi_write_stage", style="List Number")

doc.save("report.docx")
```

### Table

```python
table = doc.add_table(rows=1, cols=3)
table.style = "Table Grid"

# Header row
hdr = table.rows[0].cells
hdr[0].text = "Component"
hdr[1].text = "LLM?"
hdr[2].text = "Implementation"

# Data rows
for component, llm, impl in data:
    row = table.add_row().cells
    row[0].text = component
    row[1].text = llm
    row[2].text = impl
```

### Inline formatting (bold, italic)

```python
p = doc.add_paragraph()
run = p.add_run("Important: ")
run.bold = True
p.add_run("never hardcode secrets.")
```

### Page break

```python
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

doc.add_page_break()
```

### Insert image

```python
from docx.shared import Inches

doc.add_picture("diagram.png", width=Inches(5.5))
```

---

## Markdown to PDF — Formatting Tips

Use standard Markdown. pandoc handles:

- `# H1`, `## H2`, `### H3` → headings
- `**bold**`, `*italic*`
- ` ```python\n...\n``` ` → syntax-highlighted code block
- `| col | col |` → table
- `- item` → bullet list
- `1. item` → numbered list
- `> quote` → blockquote

### YAML front matter (pandoc only)

```markdown
---
title: "Musubi Report"
author: "Engineering Team"
date: "2026-04-17"
geometry: margin=2cm
fontsize: 11pt
---

# Section 1
...
```

---

## Naming and Storage

- Temporary outputs go to system temp dir — move to repo if committing.
- Committed diagrams/docs live in `docs/` at the repo root.
- Name pattern: `docs/{type}/{YYYY-MM-DD}-{slug}.{ext}`
  - Example: `docs/architecture/2026-04-17-harness-overview.drawio`
  - Example: `docs/reports/2026-04-17-day1-summary.pdf`
