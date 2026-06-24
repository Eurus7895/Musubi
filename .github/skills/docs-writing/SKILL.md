---
name: docs-writing
description: Write or update technical documentation — READMEs, design docs, ADRs, API references, user guides. Use when the user asks for documentation, prose explanation, or to update existing docs. NOT for code-level docstrings (the code agent handles those).
applies-to:
  doc_tools: [sphinx, mkdocs, mdbook]
musubi-tier: substrate
expires-when: never (skills are the catalog the model pulls from)
---

## Purpose

Produce technical documentation that a reader can pick up cold and act on
within ~10 minutes. Match the project's existing doc tool (sphinx / mkdocs
/ mdbook / plain Markdown) without inventing a new format.

## Procedure

1. **Locate the audience.** Identify who reads this doc — a future
   contributor, an external integrator, a user. The audience determines
   tone, depth, and assumed knowledge.
2. **Pick the existing convention.** Read 1-2 sibling docs first.
   Filenames, heading depth, list style, code-block language tags, link
   shape — copy them. Do not invent.
3. **Open with what + why in ≤3 sentences.** The first paragraph
   answers: *what is this thing, why does it exist, who is it for*. No
   throat-clearing.
4. **Lead with the load-bearing thing.** Quickstart code, the one
   diagram, the table that summarises the choice. Reference docs
   especially: examples first, full prose later.
5. **Cross-link, don't duplicate.** When concept X has a canonical doc
   elsewhere, link to it. Duplicated explanations rot independently.
6. **Verify the examples run.** Copy every command/code block out and
   execute it. A doc that lies is worse than no doc.

## Output contract

Returned content is **Markdown** (or the project's doc-tool format), with:

- `#` H1 title in sentence case (one only).
- Opening 1-3 sentence summary before any subheading.
- At least one usage example or concrete artefact (command, config
  snippet, diagram).
- Cross-links to ≥1 existing doc when an adjacent concept already has one.
- No emoji unless the project's existing docs use them.

## Anti-patterns

- "This document describes…" — start with the subject, not the document.
- Long bullet lists where a table would compress better.
- Writing "TODO" / "TBD" inline — either leave the section out or fill
  it in. Placeholders rot.
- Re-explaining concepts the linked doc already covers.
- Generated docs (Sphinx autodoc, etc.) mixed with hand-written prose
  in the same file without a divider.

## When NOT to use

- The user wants in-code docstrings. Use the `python` (or language)
  skill — docstrings are part of the code surface.
- The change is a single-line README typo. Just fix it.
- Architecture decision needs negotiation, not documentation. Discuss
  first, write the ADR after the decision lands.
